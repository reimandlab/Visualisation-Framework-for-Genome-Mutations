from view_testing import ViewTest
from models import Protein, KinaseGroup, Drug, DrugGroup, MIMPMutation
from models import Gene
from models import Site
from models import Kinase
from models import Mutation
from models import MC3Mutation
from models import Cancer
from database import db


def create_test_protein():

    g_x = Gene(name='Gene X')
    p = Protein(refseq='NM_0007', gene=g_x, sequence='TRAN')
    return p


def create_test_kinase(name, refseq):

    interactor = Kinase(name=name)

    kinase_gene = Gene(name='Gene of ' + interactor.name)
    kinase_protein = Protein(refseq=refseq, gene=kinase_gene)

    interactor.protein = kinase_protein

    return interactor


def create_network():
    p = create_test_protein()
    cancer = Cancer(name='Ovarian', code='OV')

    known_interactor_of_x = create_test_kinase('Kinase Y', 'NM_0009')

    kinase_mutation = Mutation(
        position=1,
        alt='T',
        meta_MC3=[MC3Mutation(cancer=cancer)]
    )

    known_interactor_of_x.protein.mutations = [kinase_mutation]

    drug = Drug(
        name='Drug targeting ' + known_interactor_of_x.name,
        drug_bank_id='DB01',
        target_genes=[known_interactor_of_x.protein.gene],
        # by default only approved drugs are shown
        groups={DrugGroup(name='approved')}
    )

    group = KinaseGroup(
        name='Group of kinases',
    )
    s = Site(
        position=1,
        type='phosphorylation',
        residue='T',
        kinases=[known_interactor_of_x],
        kinase_groups=[group]
    )
    s2 = Site(
        position=2,
        type='phosphorylation',
        residue='R',
        kinase_groups=[group]
    )
    p.sites = [s, s2]

    predicted_interactor = create_test_kinase('Kinase Z', 'NM_0002')

    protein_mutation = Mutation(
        position=2,
        alt='T',
        meta_MC3=[MC3Mutation(cancer=cancer)],
        meta_MIMP=[
            MIMPMutation(pwm=known_interactor_of_x.name, effect='loss', site=s, probability=0.1, position_in_motif=1),
            MIMPMutation(pwm=predicted_interactor.name, effect='gain', site=s, probability=0.1, position_in_motif=1)
        ]
    )

    p.mutations = [protein_mutation]
    db.session.add_all([p, drug, predicted_interactor])
    db.session.commit()

    # a new cancer was added, reload is necessary (this should not happen during normal app usage)
    from website.views.filters import cached_queries
    cached_queries.reload()


class TestNetworkView(ViewTest):

    def test_show(self):
        p = create_test_protein()
        db.session.add(p)

        response = self.client.get('/network/show/NM_0007')
        assert response.status_code == 200

    def test_empty_representation(self):
        # test case 0: does empty network returns correct JSON response?
        p = create_test_protein()
        db.session.add(p)

        for endpoint in ('representation', 'predicted_representation'):
            response = self.client.get('/network/%s/NM_0007' % endpoint)
            assert response.status_code == 200
            representation = response.json['network']
            assert representation
            assert representation['kinases'] == []
            assert representation['protein']['name'] == 'Gene X'

    def test_representation(self):
        create_network()

        response = self.client.get('/network/representation/NM_0007')
        assert response.status_code == 200
        representation = response.json['network']

        # test kinases: only interaction with Kinase Y is known (the one with Kinase X is predicted!)
        assert ['Kinase Y'] == representation['protein']['kinases']
        kinase = representation['kinases'][0]
        assert kinase['name'] == 'Kinase Y'

        # test kinase drugs
        drugs = kinase['drugs_targeting_kinase_gene']
        assert len(drugs) == 1
        drug = drugs[0]
        assert drug['name'] == 'Drug targeting Kinase Y'
        assert drug['drugbank'] == 'DB01'

        # test sites
        assert len(representation['sites']) == 2

        # test groups
        assert len(representation['kinase_groups']) == 1
        assert 'Group of kinases' == representation['kinase_groups'][0]['name']

        # representation returned in internal endpoint 'data'
        # should be the same as the one expose publicly
        response = self.client.get('/network/data/NM_0007')
        assert response.json['content']['network'] == representation

    def test_predicted_representation(self):
        create_network()

        response = self.client.get('/network/predicted_representation/NM_0007')
        assert response.status_code == 200
        representation = response.json['network']

        # there are two predicted interactions: with Kinase Y and with Kinase Z
        assert set(representation['protein']['kinases']) == {'Kinase Y', 'Kinase Z'}

        # there is only one site for which there are predictions
        assert len(representation['sites']) == 1

        assert representation['sites'][0]['impact'] == 'network-rewiring'

    def test_tsv_export(self):
        create_network()

        response = self.client.get('/network/download/NM_0007/tsv')
        content = response.data.decode('utf-8')

        lines = content.split('\n')

        # is header included?
        assert lines[0].startswith('#')

        expected_site_kinase_rows = {
            # row essential data: was_found?
            # site, site type, max impact, kinase or group, drugs
            ('1,T', 'phosphorylation', 'network-rewiring', 'Kinase Y', 'Drug targeting Kinase Y'): False,
            ('1,T', 'phosphorylation', 'proximal', 'Group of kinases', ''): False,
            ('2,R', 'phosphorylation', 'direct', 'Group of kinases', ''): False
        }

        for line in lines[1:]:
            essential_data = line.split('\t')[2:]
            key = tuple(essential_data)
            assert key in expected_site_kinase_rows.keys()
            expected_site_kinase_rows[key] = True

        assert all(expected_site_kinase_rows.values())

    def test_divide_muts_by_sites(self):
        from views.network import divide_muts_by_sites

        # check if null case works
        divide_muts_by_sites([], [])

        # one site
        s_1 = Site(position=1)
        muts_by_sites = divide_muts_by_sites([], [s_1])
        assert muts_by_sites[s_1] == []

        # full test
        s_2 = Site(position=10)
        s_3 = Site(position=20)

        muts_by_pos = {
            pos: [Mutation(position=pos)]
            for pos in (1, 2, 8, 14, 16, 30)
        }

        muts_by_pos[16].append(Mutation(position=16))

        def get_muts_from_pos(*positions):
            lists = [
                muts_by_pos[p]
                for p in positions
            ]
            return [
                mut
                for mut_list in lists
                for mut in mut_list
            ]

        muts_by_sites = divide_muts_by_sites(
            [
                mut
                for muts_on_pos_x in muts_by_pos.values()
                for mut in muts_on_pos_x
            ],
            [s_1, s_2, s_3]
        )

        assert muts_by_sites[s_1] == get_muts_from_pos(1, 2, 8)
        assert muts_by_sites[s_2] == get_muts_from_pos(8, 14, 16)
        assert muts_by_sites[s_3] == get_muts_from_pos(14, 16)
