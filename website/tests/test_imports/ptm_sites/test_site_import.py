from re import escape
from timeit import Timer
from types import SimpleNamespace as RawSite
from functools import partial

from pandas import DataFrame
from pytest import warns

from database import db, create_key_model_dict
from database_testing import DatabaseTest
from imports.protein_data import precompute_ptm_mutations
from imports.sites.site_importer import SiteImporter
from imports.sites.site_mapper import find_all, find_all_regex
from imports.sites.site_mapper import SiteMapper
from models import Protein, Gene, Mutation, MC3Mutation, MIMPMutation, Site


def test_find_all():

    background = 'Lorem ipsum dololor L'

    cases = {
        'L': [0, 20],
        'o': [1, 13, 15, 17],
        'olo': [13, 15],
        '^L': [0],
        '^Lorem': [0],
        'L$': [20],
        ' L$': [19],
        'not matching': []
    }

    for query, expected_result in cases.items():
        assert find_all(background, query) == expected_result

    for case in cases:

        regexp_time = Timer(partial(find_all_regex, background, case)).timeit()
        custom_time = Timer(partial(find_all, background, case)).timeit()

        assert find_all_regex(background, case) == find_all(background, case)

        assert custom_time < regexp_time
        print(
            f'find_all() was faster than find_all_regex() '
            f'by {custom_time / regexp_time * 100}%')


def create_importer(*args, offset=7, **kwargs):

    class MinimalSiteImporter(SiteImporter):

        source_name = 'DummySource'
        site_offset = offset

        def load_sites(self, *args, **kwargs):
            return []

        site_types = []

    return MinimalSiteImporter(*args, **kwargs)


def group_by_isoform(sites: DataFrame):
    return {site.refseq: site for site in sites.itertuples(index=False)}


class TestImport(DatabaseTest):

    def test_extract_sequence_and_offset(self):
        protein = Protein(refseq='NM_0001', sequence='MSSSGTPDLPVLLTDLKIQYTKIFINNEWHDSVSGK')
        db.session.add(protein)

        importer = create_importer(offset=7)

        cases = [
            [RawSite(position=2, refseq='NM_0001', residue='S'), '^MSSSGTPDL', 1],
            [RawSite(position=10, refseq='NM_0001', residue='P'), 'SSGTPDLPVLLTDLK', 7]
        ]

        for site, sequence, offset in cases:
            assert importer.extract_site_surrounding_sequence(site) == sequence
            assert importer.determine_left_offset(site) == offset

    def test_precompute_ptm_mutations(self):
        protein = Protein(refseq='NM_0001', sequence='MSSSGTPDLPVLLTDLKIQYTKIFINNEWHDSVSGK')
        db.session.add(protein)

        site = Site(position=2, residue='S', protein=protein)
        db.session.add(site)

        mutations = {
            1: Mutation(position=1, alt='X', protein=protein),
            2: Mutation(position=2, alt='X', protein=protein),
            3: Mutation(position=3, alt='X', protein=protein)
        }

        metadata = [
            # confirmed ptm mutation
            MC3Mutation(mutation=mutations[1]),
            # confirmed ptm mutation with a non-confirmed extra annotation
            MC3Mutation(mutation=mutations[2]),
            MIMPMutation(mutation=mutations[2]),
            # non-confirmed ptm mutation
            MIMPMutation(mutation=mutations[3])
        ]
        db.session.add_all(metadata)
        db.session.commit()

        assert not mutations[1].precomputed_is_ptm
        assert not mutations[2].precomputed_is_ptm
        assert not mutations[3].precomputed_is_ptm

        precompute_ptm_mutations.load()

        assert mutations[1].precomputed_is_ptm
        assert mutations[2].precomputed_is_ptm
        assert not mutations[3].precomputed_is_ptm

    def test_map_site_to_isoform(self):

        mapper = SiteMapper([], lambda s: f'{s.position}{s.sequence}')

        site = RawSite(sequence='FIN', position=6, left_sequence_offset=1)
        protein = Protein(sequence='LKIQYTKIFINNEWHDSVSG')

        assert mapper.map_site_to_isoform(site, protein) == [10]

        with warns(UserWarning, match='More than one match for: 2KI'):
            site = RawSite(sequence='KI', position=2, left_sequence_offset=0)
            assert mapper.map_site_to_isoform(site, protein) == [2, 7]

    def test_mapping(self):

        gene_a = Gene(name='A', isoforms=[
            # the full isoform of gene A
            Protein(refseq='NM_01', sequence='AAAAAAAAAXAA'),
            # a trimmed isoform of gene A
            Protein(refseq='NM_02', sequence='AAAXAA'),
        ])
        gene_b = Gene(name='B', isoforms=[
            Protein(refseq='NM_03', sequence='BBBBBBBBBYBB'),
            Protein(refseq='NM_04', sequence='BBBYBB'),
        ])
        db.session.add_all([gene_a, gene_b])

        # whoops, NM_03 has be accidentally removed (!)
        db.session.delete(Protein.query.filter_by(refseq='NM_03').one())
        db.session.commit()

        mapper = SiteMapper(
            create_key_model_dict(Protein, 'refseq'),
            lambda s: f'{s.position}{s.residue}'
        )

        sites = DataFrame([
            {
                'name': 'good site A',
                'gene': 'A',
                'refseq': 'NM_01',
                'position': 10,
                'sequence': 'AXA',
                'residue': 'X',
                'left_sequence_offset': 1
            },
            {
                'name': 'lost isoform',
                'gene': 'B',
                'refseq': 'NM_03',
                'position': 10,
                'sequence': 'BYB',
                'residue': 'Y',
                'left_sequence_offset': 1
            }
        ]).set_index('name')

        with warns(
                UserWarning,
                match=escape(
                    'Site 10X was found on position(s): 4;'
                    ' some are quite far away from the position in original isoform: 10'
                )
        ):
            mapped_sites = mapper.map_sites_by_sequence(sites)

        sites_by_isoform = group_by_isoform(mapped_sites)

        # one from NM_01 (defined), from NM_02 (mapped), from NM_04 (mapped)
        assert len(mapped_sites) == 3
        assert set(sites_by_isoform) == {'NM_01', 'NM_02', 'NM_04'}

        assert sites_by_isoform['NM_01'].residue == sites_by_isoform['NM_02'].residue == 'X'
        assert sites_by_isoform['NM_01'].position == 10
        assert sites_by_isoform['NM_02'].position == 4

        assert sites_by_isoform['NM_04'].residue == 'Y'
        assert sites_by_isoform['NM_04'].position == 4

        # will the mapping to NM_02 still work if we remove 'gene' column?
        sites.drop(columns=['gene'], inplace=True)
        mapped_sites = mapper.map_sites_by_sequence(sites)
        sites_by_isoform = group_by_isoform(mapped_sites)

        assert len(mapped_sites) == 2
        assert set(sites_by_isoform) == {'NM_01', 'NM_02'}

    def test_edge_cases_mapping(self):

        gene_t = Gene(name='T', isoforms=[
            #                                 123456789
            Protein(refseq='NM_01', sequence='AXAXAYAYA'),
            # C-terminal part was trimmed
            Protein(refseq='NM_02', sequence='AXAXA'),
            # N-terminal part was trimmed
            Protein(refseq='NM_03', sequence='AYAYA'),
        ])
        db.session.add(gene_t)
        db.session.commit()

        mapper = SiteMapper(
            create_key_model_dict(Protein, 'refseq'),
            lambda s: f'{s.position}{s.residue}'
        )

        # all sites in NM_01, the idea is to test the edge cases
        sites = DataFrame.from_dict(data={
            'site at N-terminus edge': ('T', 'NM_01', 1, '^AX', 'A', 1),
            'site at C-terminus edge': ('T', 'NM_01', 9, 'YA$', 'A', 1),
        }, orient='index')

        sites.columns = [
            'gene', 'refseq', 'position', 'sequence', 'residue', 'left_sequence_offset'
        ]

        mapped_sites = mapper.map_sites_by_sequence(sites)

        assert len(mapped_sites) == 4

    def test_wrong_position(self):

        gene_w = Gene(name='W', isoforms=[
            #                                 123456789
            Protein(refseq='NM_01', sequence='AAAXAAAAA'),
        ])

        db.session.add(gene_w)
        db.session.commit()

        mapper = SiteMapper(
            create_key_model_dict(Protein, 'refseq'),
            lambda s: f'{s.position}{s.residue}'
        )

        sites = DataFrame([
            {
                'name': 'first side',
                'gene': 'W',
                'refseq': 'NM_01',
                # should have been 4!
                'position': 5,
                'sequence': 'AXA',
                'residue': 'X',
                'left_sequence_offset': 1
            }
        ]).set_index('name')

        with warns(
                UserWarning,
                match=escape(
                    'Site: 5X does not appear on the exact given position in NM_01 isoform,'
                    ' though it was re-mapped to: [4]'
                )
        ):
            mapped_sites = mapper.map_sites_by_sequence(sites)

        assert len(mapped_sites) == 1

    def test_mapping_both_isoforms_annotated(self):
        """
        Usually the sites come annotated on only one isoform of the protein.
        However, they can be annotated to two sequences; it often happens
        even when the source database annotates the only once if it uses
        UniProt sequence identifier; this is because a single UniProt sequence
        can be mapped to multiple refseq sequences. Either way, we end up with
        two sites annotated on identical sequences of two isoforms.

        In the following step, the naive mapping algorithm would cross-map them,
        which is a correct behaviour because they are indeed identical; however,
        starting from single site, mapped to one UniProt sequence which maps onto
        two RefSeq sequences we would then end up with four records (two pairs of
        identical records). Therefore, a de-duplication needs to be carried out.
        """

        gene_a = Gene(name='A', isoforms=[
            # the full isoform of gene A
            Protein(refseq='NM_01', sequence='AAAAAAAAAXAA'),
            # a trimmed isoform of gene A
            Protein(refseq='NM_02', sequence='AAAXAA'),
        ])
        db.session.add(gene_a)
        db.session.commit()

        sites = DataFrame([
            {
                'name': 'first side',
                'gene': 'A',
                'refseq': 'NM_01',
                'position': 10,
                'sequence': 'AXA',
                'residue': 'X',
                'left_sequence_offset': 1
            },
            {
                'name': 'second site',
                'gene': 'A',
                'refseq': 'NM_02',
                'position': 4,
                'sequence': 'AXA',
                'residue': 'X',
                'left_sequence_offset': 1
            }
        ]).set_index('name')

        mapper = SiteMapper(
            create_key_model_dict(Protein, 'refseq'),
            lambda s: f'{s.position}{s.residue}'
        )

        result = mapper.map_sites_by_sequence(sites)

        assert len(result) == 2
