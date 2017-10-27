from view_testing import ViewTest
from models import Protein, Disease, MIMPMutation, Kinase
from models import Gene
from models import Mutation
from models import Cancer
from models import MC3Mutation
from models import InheritedMutation
from models import ClinicalData
from models import The1000GenomesMutation
from models import ExomeSequencingMutation
from database import db


def test_protein_data():
    return {
        'refseq': 'NM_000123',
        'gene': Gene(name='SomeGene'),
        'sequence': 'MART'
    }


def create_test_mutations():
    data = [
        Mutation(
            position=1,
            alt='K',
            meta_MC3=[
                MC3Mutation(
                    cancer=Cancer(name='Ovarian', code='OV'),
                    count=1
                ),
                MC3Mutation(
                    cancer=Cancer(name='Breast', code='BRCA'),
                    count=1
                )
            ]
        ),
        Mutation(
            position=2,
            alt='K',
            meta_ClinVar=InheritedMutation(
                clin_data=[
                    ClinicalData(
                        disease=Disease(name='Disease X'),
                        sig_code=1
                    ),
                    ClinicalData(
                        disease=Disease(name='Disease Y'),
                        sig_code=1
                    )
                ]
            ),
            meta_MIMP=[
                MIMPMutation(pwm='Kinase with protein', probability=0.1),
                MIMPMutation(pwm='Kinase without protein', probability=0.1),
                MIMPMutation(pwm='Unknown kinase', probability=0.1)
            ]
        ),
        Mutation(
            position=3,
            alt='K',
            meta_1KGenomes=[The1000GenomesMutation(
                maf_afr=0.5,
                maf_eur=0.2
            )]
        ),
        Mutation(
            position=4,
            alt='K',
            meta_ESP6500=[ExomeSequencingMutation(
                maf_ea=0.5
            )]
        )
    ]

    return data


class TestSequenceView(ViewTest):

    def test_show(self):

        p = Protein(**test_protein_data())
        p.mutations = create_test_mutations()
        kinases = [
            Kinase(name='Kinase with protein', protein=Protein(refseq='NM_0001')),
            Kinase(name='Kinase without protein')
        ]
        db.session.add_all(kinases)
        db.session.add(p)

        from website.views.filters import cached_queries
        cached_queries.reload()

        response = self.client.get('/sequence/show/NM_000123')

        assert response.status_code == 200
        assert b'SomeGene' in response.data
        assert b'NM_000123' in response.data

        response = self.client.get('/sequence/representation_data/NM_000123')

        assert response.status_code == 200
        assert b'MART' in response.data
        assert response.content_type == 'application/json'

        representation = response.json['content']
        assert representation['tracks']
        assert representation['mutation_table']

        # for default source (cancer) one mutation is expected
        assert len(representation['mutations']) == 1

        # no sites were given
        assert len(representation['sites']) == 0

        # let's check clinvar source
        response = self.client.get('/sequence/representation_data/NM_000123?filters=Mutation.sources:in:ClinVar')
        muts = response.json['content']['mutations']
        assert len(muts) == 1
        assert len(muts[0]['meta']['MIMP']) == 3

    def test_details(self):

        p = Protein(**test_protein_data())
        p.mutations = create_test_mutations()

        db.session.add(p)

        expected_source_meta = {
            'MC3': ['OV', 'BRCA'],
            'ClinVar': ['Disease X', 'Disease Y'],
            '1KGenomes': ['African', 'European'],
            'ESP6500': ['European American']
        }

        uri = '/protein/details/NM_000123'

        for source, expected_meta in expected_source_meta.items():
            response = self.client.get(
                uri + '?filters=Mutation.sources:in:%s' % source
            )
            assert response.status_code == 200
            assert response.content_type == 'application/json'
            json_response = response.json
            assert json_response['refseq'] == 'NM_000123'
            assert set(json_response['meta']) == set(expected_meta)
            assert json_response['muts_count'] == 1

        response = self.client.get(uri + '?filters=Mutation.sources:in:ESP6500;Mutation.populations_ESP6500:in:African American')
        assert response.json['muts_count'] == 0

        response = self.client.get(uri + '?filters=Mutation.sources:in:ESP6500;Mutation.populations_ESP6500:in:European American')
        assert response.json['muts_count'] == 1
