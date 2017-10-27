import re
from io import BytesIO

from view_testing import ViewTest, relative_location
from tests.miscellaneous import mock_proteins_and_genes

from database import db
from models import Gene, Pathway, GeneList, MC3Mutation, Disease, InheritedMutation, ClinicalData
from models import Protein
from models import UsersMutationsDataset
from models import Site
from models import Mutation


# base on example from specification in version 4.3:
# http://samtools.github.io/hts-specs/VCFv4.3.pdf
VCF_FILE_CONTENT = b"""\
##fileformat=VCFv4.3
##fileDate=20090805
##source=myImputationProgramV3.1
##reference=file:///seq/references/1000GenomesPilot-NCBI36.fasta
##contig=<ID=20,length=62435964,assembly=B36,md5=f126cdf8a6e0c7f379d618ff66beb2da,species="Homo sapiens",taxonomy=x>
##phasing=partial
##INFO=<ID=NS,Number=1,Type=Integer,Description="Number of Samples With Data">
##INFO=<ID=DP,Number=1,Type=Integer,Description="Total Depth">
##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">
##INFO=<ID=AA,Number=1,Type=String,Description="Ancestral Allele">
##INFO=<ID=DB,Number=0,Type=Flag,Description="dbSNP membership, build 129">
##INFO=<ID=H2,Number=0,Type=Flag,Description="HapMap2 membership">
##FILTER=<ID=q10,Description="Quality below 10">
##FILTER=<ID=s50,Description="Less than 50% of samples have data">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype Quality">
##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read Depth">
##FORMAT=<ID=HQ,Number=2,Type=Integer,Description="Haplotype Quality">
#CHROM POS     ID        REF    ALT     QUAL FILTER INFO                              FORMAT      NA00001        NA00002        NA00003
20     14370   rs6054257 G      A       29   PASS   NS=3;DP=14;AF=0.5;DB;H2           GT:GQ:DP:HQ 0|0:48:1:51,51 1|0:48:8:51,51 1/1:43:5:.,.
20     17330   .         T      A       3    q10    NS=3;DP=11;AF=0.017               GT:GQ:DP:HQ 0|0:49:3:58,50 0|1:3:5:65,3   0/0:41:3
20     1110696 rs6040355 A      G,T     67   PASS   NS=2;DP=10;AF=0.333,0.667;AA=T;DB GT:GQ:DP:HQ 1|2:21:6:23,27 2|1:2:0:18,2   2/2:35:4
20     1230237 .         T      .       47   PASS   NS=3;DP=13;AA=T                   GT:GQ:DP:HQ 0|0:54:7:56,60 0|0:48:4:51,51 0/0:61:2
20     1234567 microsat1 GTC    G,GTCT  50   PASS   NS=3;DP=9;AA=G                    GT:GQ:DP    0/1:35:4       0/2:17:2       1/1:40:3\
"""


def get_entry_and_check_type(response, type_name):
    """For use when exactly one result of given type is expected"""
    entries = response.json['entries']
    assert len(entries) == 1
    assert entries[0]['type'] == type_name
    return entries[0]


def entries_with_type(response, type_name):
    """For use when more than one result of given type is expected"""
    return list(filter(lambda entry: entry['type'] == type_name, response.json['entries']))


class TestSearchView(ViewTest):

    def view_module(self):
        from website.views import search
        return search

    def visit_returned_urls(self, response):
        for entry in response.json['entries']:
            if 'url' in entry:
                r = self.client.get(entry['url'])
                assert r.status_code == 200

    def test_search_proteins(self):
        from views.search import search_proteins

        # create 15 genes and proteins
        mock_proteins_and_genes(15)

        # control: do we start with the mocked proteins not others?
        assert not search_proteins('TP53')

        # does respect limit? does symbol search work?
        results = search_proteins('Gene', 10)

        assert results
        assert len(results) == 10

        assert results[0].name.startswith('Gene')

        # are results sorted?
        db.session.add_all([
            Gene(name=name, preferred_isoform=Protein(refseq='NM_%s' % 20 * i))
            for i, name in enumerate(['TPK', 'TPKK'])
        ])
        results = search_proteins('TPK', 2)
        assert results[0].name == 'TPK'
        assert results[0].best_score < results[1].best_score

        # does include both: refseq and symbol search?
        assert search_proteins('NM_0003', 1)

        # can we change subset of searched features?
        assert not search_proteins('NM_0003', 1, features=['gene_symbol'])
        assert not search_proteins('Gene', 1, features=['refseq'])

    def test_autocomplete_proteins(self):
        mock_proteins_and_genes(5)
        g = Gene(name='Gene: 6', preferred_isoform=Protein(refseq='NM_006'))
        db.session.add(g)

        for route in ('autocomplete_proteins', 'proteins'):
            for accepted_gene_2_query in ('Gene_2', 'Gene', 'gene', 'Gene_2 ', ' gene', 'gene%20'):
                print(route, accepted_gene_2_query)
                response = self.client.get(
                    'search/%s?filters=Search.query:eq:%s' % (route, accepted_gene_2_query),
                    follow_redirects=True
                )
                assert response.status_code == 200
                assert b'Gene_2' in response.data
                assert b'NM_0002' in response.data

        response = self.client.get(
            'search/autocomplete_proteins?filters=Search.query:eq:Gene: ',
            follow_redirects=True
        )
        assert response.status_code == 200
        assert b'Gene: 6' in response.data

    def search_mutations(self, **data):
        return self.client.post(
            '/search/mutations',
            data=data
        )

    def test_search_mutations(self):

        s = Site(position=13, type='methylation')
        p = Protein(refseq='NM_007', id=7, sites=[s], sequence='XXXXXXXXXXXXV')

        m_in_site = Mutation(protein=p, position=13, alt='V')
        m_out_site = Mutation(protein=p, position=50, alt='K')

        db.session.add(p)

        # points to the same location as first record in VCF_FILE_CONTENT
        test_query = 'chr20 14370 G A'

        from database import bdb

        # map the first genomic mutation from VCF_FILE_CONTENT
        # to some (mocked) protein mutation
        bdb.add_genomic_mut('20', 14370, 'G', 'A', m_in_site, is_ptm=True)

        #
        # basic test - is appropriate mutation in results?
        #
        response = self.search_mutations(mutations=test_query)

        assert response.status_code == 200

        # this mutation is exactly at a PTM site and should be included in results
        assert '<td>{0}</td>'.format(m_in_site.alt).encode() in response.data
        # this mutation lies outside of a PTM site - be default should be filtered out
        assert '<td>{0}</td>'.format(m_out_site.alt).encode() not in response.data

        #
        # count test - is mutation for this query annotated as shown twice?
        #
        response = self.search_mutations(
            mutations='{0}\n{0}'.format(test_query)
        )

        assert response.status_code == 200
        assert b'<td>2</td>' in response.data

        #
        # VCF file test
        #
        response = self.client.post(
            '/search/mutations',
            content_type='multipart/form-data',
            data={
                'vcf-file': (BytesIO(VCF_FILE_CONTENT), 'exemplar_vcf.vcf')
            }
        )

        assert response.status_code == 200
        assert b'NM_007' in response.data

    def test_autocomplete_all_proteins(self):
        # MC3 GeneList is required as a target (a href for links) where users will be pointed
        # after clicking of cancer autocomplete suggestion
        gene_list = GeneList(name='TCGA', mutation_source_name=MC3Mutation.name)
        db.session.add(gene_list)

        mock_proteins_and_genes(15)

        response = self.client.get(
            'search/autocomplete_all?q=%s' % 'Gene',
            follow_redirects=True
        )
        assert response.status_code == 200
        assert response.json['entries'][0]['name'].startswith('Gene')

    def test_autocomplete_all(self):

        # MC3 GeneList is required as a target (a href for links) where users will be pointed
        # after clicking of cancer autocomplete suggestion. Likewise with the ClinVar list.
        db.session.add_all([
            GeneList(name=name, mutation_source_name=detail_class.name)
            for name, detail_class in [
                ('TCGA', MC3Mutation), ('ClinVar', InheritedMutation)
            ]
        ])

        g = Gene(name='BR')
        p = Protein(id=1, refseq='NM_007', gene=g, sequence='XXXXXV')
        g.preferred_isoform = p     # required for gene search to work - genes without preferred isoforms are ignored
        mut = Mutation(protein=p, position=6, alt='E')
        db.session.add_all([mut, p, g])

        def autocomplete(query):
            r = self.client.get('/search/autocomplete_all/?q=' + query)
            self.visit_returned_urls(r)
            return r

        from database import bdb_refseq, bdb
        bdb_refseq['BR V6E'] = ['NM_007']  # required for mutation search
        bdb.add_genomic_mut('1', 10000, 'T', 'C', mut)

        # Gene and mutations

        response = autocomplete('BR V6E')
        entry = get_entry_and_check_type(response, 'aminoacid mutation')
        assert entry

        response = autocomplete('BR V6')
        entry = get_entry_and_check_type(response, 'message')
        assert 'Awaiting for <code>{alt}</code>' in entry['name']

        response = autocomplete('BR V')
        entry = get_entry_and_check_type(response, 'message')
        assert 'Awaiting for <code>{pos}{alt}</code>' in entry['name']

        response = autocomplete('B')
        entry = get_entry_and_check_type(response, 'gene')
        assert 'BR' == entry['name']

        # genomic mutation
        response = autocomplete('chr1 10000 T C')
        entry = get_entry_and_check_type(response, 'nucleotide mutation')
        assert entry and entry['input'] == 'CHR1 10000 T C'

        # is the search falling back to the other strand?
        response = autocomplete('chr1 10000 A G')
        entry = get_entry_and_check_type(response, 'nucleotide mutation')
        assert entry and entry['input'] == 'complement of CHR1 10000 A G'

        prompt = 'Awaiting for mutation in <code>{chrom} {pos} {ref} {alt}</code> format'

        for prompt_invoking_query in ['chr1', 'chr1 ', 'chr1 40', 'chr1 40 ', 'chr1 40 T']:
            response = autocomplete(prompt_invoking_query)
            entry = get_entry_and_check_type(response, 'message')
            assert entry['name'] == prompt

        # Pathways

        pathways = [
            Pathway(description='Activation of RAS in B cells', reactome=1169092),
            Pathway(description='abortive mitotic cell cycle', gene_ontology=33277),
            Pathway(description='amacrine cell differentiation', gene_ontology=35881),
            Pathway(description='amniotic stem cell differentiation', gene_ontology=97086)
        ]

        db.session.add_all(pathways)

        # test partial matching and Reactome id pathways search
        for ras_activation_query in ['Activation', 'REAC:1', 'REAC:1169092']:
            response = autocomplete(ras_activation_query)
            entry = get_entry_and_check_type(response, 'pathway')
            assert entry['name'].startswith('Activation of RAS in B cells')

        # test Gene Ontology search:
        response = autocomplete('GO:33')
        go_pathway = get_entry_and_check_type(response, 'pathway')
        assert go_pathway['name'] == 'abortive mitotic cell cycle (GO:33277)'

        # check if multiple pathways are returned
        response = autocomplete('differentiation')
        assert len(response.json['entries']) == 2

        # check if both genes an pathways are returned simultaneously
        # there should be: a pathway ('a>b<ortive...') and the >B<R gene
        response = autocomplete('b')
        entries = response.json['entries']
        names = [entry['name'] for entry in entries]
        assert all([name in names for name in ['BR', 'abortive mitotic cell cycle']])

        # check if "search more pathways" is displayed
        response = autocomplete('cell')    # cell occurs in all four of added pathways;
        # as a limit of pathways shown is 3, we should get a "show more" link
        links = entries_with_type(response, 'see_more')
        assert len(links) == 1
        assert links[0]['name'] == 'Show all pathways matching <i>cell</i>'

        # test case insensitive text search
        response = autocomplete('AMNIOTIC STEM')
        pathways = entries_with_type(response, 'pathway')
        assert len(pathways) == 1
        assert pathways[0]['name'] == 'amniotic stem cell differentiation'

        # Disease
        disease_names = [
            'Cystic fibrosis', 'Polycystic kidney disease 2',
            'Frontotemporal dementia', 'Cataract, nuclear total'
        ]
        diseases = {name: Disease(name=name) for name in disease_names}
        db.session.add_all(diseases.values())

        response = autocomplete('cystic')
        cystic_matching = entries_with_type(response, 'disease')
        # both 'Cystic fibrosis' and PKD2 should match
        assert len(cystic_matching) == 2

        # is comma containing disease name properly linked?
        response = autocomplete('Cataract')
        cataract = get_entry_and_check_type(response, 'disease')
        assert cataract['name'] == 'Cataract, nuclear total'

        # Gene mutation in disease

        # test suggestions
        response = autocomplete('cystic ')
        entry = entries_with_type(response, 'message')[0]
        assert re.match('Do you wish to search for (.*?) mutations\?', entry['name'])

        # currently there are no mutations associated with any disease
        # so the auto-completion should not return any results
        response = autocomplete('cystic in ')
        assert not response.json['entries']

        # let's add a mutation
        m = Mutation(protein=p, position=1, alt='Y')
        bdb_refseq['BR X1Y'] = ['NM_007']
        # note: sig_code is required here
        data = ClinicalData(disease=diseases['Cystic fibrosis'], sig_code=1)
        disease_mutation = InheritedMutation(mutation=m, clin_data=[data])
        db.session.add_all([m, data, disease_mutation])

        # should return '.. in BR' suggestion now.
        for query in ['cystic in', 'cystic in ']:
            response = autocomplete(query)
            result = get_entry_and_check_type(response, 'disease_in_protein')
            assert result['gene'] == 'BR'
            assert result['name'] == 'Cystic fibrosis'

        # both gene search and refseq search should yield the same, non-empty results
        results = []

        for query in ['cystic in BR', 'cystic in NM_007', 'cystic in 007']:
            response = autocomplete(query)
            result = get_entry_and_check_type(response, 'disease_in_protein')
            results.append(result)

        assert all(r == result for r in results) and result

    def test_save_search(self):
        self.login('user@domain.org', 'password', create=True)

        save_response, test_query, dataset = self.basic_save_search()

        assert save_response.status_code == 200

        # if user saved a dataset, it should be listed in his datasets
        browse_response = self.client.get('/my_datasets/')
        assert browse_response.status_code == 200
        assert b'Test Dataset' in browse_response.data

        # and it should be accessible directly
        browse_response = self.client.get('search/saved/%s' % dataset.uri)
        assert browse_response.status_code == 200
        assert b'Test Dataset' in browse_response.data

        self.logout()

        # forbidden from outside
        browse_response = self.client.get('search/saved/%s' % dataset.uri)
        assert browse_response.status_code == 401

        # forbidden for strangers
        self.login('other_user@domain.org', 'password', create=True)
        browse_response = self.client.get('search/saved/%s' % dataset.uri)
        assert browse_response.status_code == 401
        self.logout()

        # it's still allowed to save data on server without logging in,
        # but then user will not be able to browse these as datasets.
        unauthorized_save_response = self.search_mutations(
            mutations=test_query,
            store_on_server=True,
            dataset_name='Test Dataset'
        )

        assert unauthorized_save_response.status_code == 200

    def test_save_search_with_celery(self):
        self.app.config['USE_CELERY'] = True
        self.login('user@domain.org', 'password', create=True)

        save_response, test_query, dataset = self.basic_save_search()

        location = relative_location(save_response)
        assert save_response.status_code == 302
        assert location.startswith('/search/progress/')

        response = self.client.get(location, follow_redirects=True)
        assert response.status_code == 200

        raw_response = self.client.get(
            location.replace('/progress/', '/raw_progress/'),
            follow_redirects=True
        )

        assert raw_response.json['status'] == 'PENDING'
        assert raw_response.json['progress'] == 0

        # TODO: this test needs to include loading and final redirection
        # attempts made so far failed, but pytest celery_session_worker
        # and CELERY_TASK_ALWAYS_EAGER are the most promising ideas

        self.app.config['USE_CELERY'] = False

    def basic_save_search(self, name='Test Dataset', query='chr18 19282310 T C'):
        save_response = self.search_mutations(
            mutations=query,
            store_on_server=True,
            dataset_name=name
        )
        dataset = UsersMutationsDataset.query.filter_by(name=name).one()
        return save_response, query, dataset

    def test_remove_dataset(self):
        self.login('user@domain.org', 'password', create=True)

        # login a user and add a basic dataset
        _, _, dataset = self.basic_save_search()

        # the user who added the dataset is allowed to delete it
        with self.assert_flashes('Successfully removed <b>%s</b> dataset.' % dataset.name):
            response = self.client.get('search/remove_saved/%s' % dataset.uri, follow_redirects=True)
            assert response.status_code == 200

        # so we should be able to create the dataset again now
        response, _, dataset = self.basic_save_search()
        assert response.status_code == 200

        # but a stranger should not be able to remove it
        self.logout()
        response = self.client.get('search/remove_saved/%s' % dataset.uri, follow_redirects=True)
        assert response.status_code == 401

        # nor a different user
        self.login('other_user@domain.org', 'password', create=True)
        response = self.client.get('search/remove_saved/%s' % dataset.uri, follow_redirects=True)
        assert response.status_code == 401

