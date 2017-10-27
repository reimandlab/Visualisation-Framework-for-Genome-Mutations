from database import db
from database_testing import DatabaseTest
from models import ProteinReferences, UniprotEntry, Protein, Gene
from search.gene import GeneNameSearch, UniprotSearch, SummarySearch, ProteinNameSearch
from tests.miscellaneous import mock_proteins_and_genes


class TestGeneSearch(DatabaseTest):

    def test_refseq(self):
        from search.gene import RefseqGeneSearch

        # create 15 genes and proteins
        mock_proteins_and_genes(10)

        search = RefseqGeneSearch().search

        # negative control
        for phase in ['9999', 'NM_00000', 'Gene']:
            assert not search(phase)

        # limiting
        results = search('NM_', limit=5)
        assert len(results) == 5

        assert results[0].name.startswith('Gene')

        # test the search itself
        for refseq in ['NM_0003', 'nm_0003', '0003']:
            results = search(refseq)
            assert len(results) == 1
            assert results[0].name == 'Gene_3'

            isoforms = results[0].matched_isoforms
            assert len(isoforms) == 1
            assert isoforms.pop().refseq == 'NM_0003'

        db.session.add_all([
            Gene(name='Gene X', isoforms=[
                Protein(refseq='NM_000301'),
                Protein(refseq='NM_000302'),
            ]),
            Gene(name='Gene Y', isoforms=[
                Protein(refseq='NM_000309')
            ])
        ])

        # so there are three genes with isoforms starting with NM_0003
        # (those are Gene_3, Gene X, Gene Y). Let see if limiting work
        # well when applied per-gene.

        queries = {
            'NM_0003': 2,
            'NM_00030': 2,
            'NM_000301': 1,
            'NM_000302': 1
        }

        for query, expected_result in queries.items():
            assert len(search(query, limit=2)) == expected_result

    def test_gene_symbol(self):
        from search.gene import SymbolGeneSearch

        # create 15 genes and proteins
        mock_proteins_and_genes(10)

        search = SymbolGeneSearch().search

        # negative control
        assert not search('TP53')   # symbol absent
        assert not search('NM_0000')   # refseq not a symbol

        # limiting
        results = search('Gene', limit=5)
        assert len(results) == 5

        assert results[0].name.startswith('Gene')

        # should not be case sensitive
        results = search('gene')
        assert results

        # should ignore flanking whitespaces
        for query in ('gene ', 'gene   ', ' gene', ' gene '):
            assert search(query)

    def test_gene_name(self):
        # this is exactly the same as gene_symbol
        mock_proteins_and_genes(6)

        search = GeneNameSearch().search
        results = search('Full name of gene', limit=5)

        assert len(results) == 5
        assert results[0].full_name.startswith('Full name of gene')

    def test_summary(self):
        mock_proteins_and_genes(2)
        protein = Protein.query.filter_by(refseq='NM_0001').one()
        protein.summary = 'This is an important protein for the FooBar pathway'

        search = SummarySearch(minimal_length=3).search

        for accepted in ['FooBar', 'foobar', 'foobar pathway']:
            assert search(accepted)

        # too short
        assert not search('an')

        # negative control
        assert not search('cancer')

    def test_protein_name(self):
        mock_proteins_and_genes(2)
        protein = Protein.query.filter_by(refseq='NM_0001').one()
        protein.full_name = 'Important protein'

        search = ProteinNameSearch().search

        assert search('important')

    def test_uniprot(self):

        uniprot = UniprotEntry(accession='P04637')
        references = ProteinReferences(uniprot_entries=[uniprot])
        protein = Protein(refseq='NM_000546', external_references=references)
        gene = Gene(name='TP53', isoforms=[protein])
        db.session.add_all([gene, references, protein, uniprot])
        db.session.commit()

        search = UniprotSearch().search

        results = search('P04637')
        assert len(results) == 1
        assert results[0].name == 'TP53'
