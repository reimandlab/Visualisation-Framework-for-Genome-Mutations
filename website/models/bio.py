from collections import defaultdict
from collections import OrderedDict
from collections import UserList
from sqlalchemy import and_
from sqlalchemy import or_
from sqlalchemy import func
from sqlalchemy import case
from sqlalchemy.sql import exists
from sqlalchemy.sql import select
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.ext.hybrid import hybrid_method
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.ext.associationproxy import association_proxy
from werkzeug.utils import cached_property
from database import db
from models import Model


class BioModel(Model):
    """Models descending from BioData are supposed to hold biology-related data

    and will be stored in a 'bio' database, separated from visualization
    settings and other data handled by 'content management system'.
    """
    __abstract__ = True
    __bind_key__ = 'bio'


def make_association_table(fk1, fk2):
    """Create an association table basing on names of two given foreign keys.

    From keys: `site.id` and `kinase.id` a table named: site_kinase_association
    will be created and it will contain two columns: `site_id` and `kinase_id`.
    """
    table_name = '%s_%s_association' % (fk1.split('.')[0], fk2.split('.')[0])
    return db.Table(
        table_name, db.metadata,
        db.Column(fk1.replace('.', '_'), db.Integer, db.ForeignKey(fk1)),
        db.Column(fk2.replace('.', '_'), db.Integer, db.ForeignKey(fk2)),
        info={'bind_key': 'bio'}    # use 'bio' database
    )


class GeneListEntry(BioModel):
    type = db.Column(db.String(32))
    gene_list_id = db.Column(db.Integer, db.ForeignKey('genelist.id'))

    p = db.Column(db.Float(precision=53))
    fdr = db.Column(db.Float(precision=53))

    gene_id = db.Column(db.Integer, db.ForeignKey('gene.id'))
    gene = db.relationship('Gene')

    __mapper_args__ = {
        'polymorphic_identity': 'entry',
        'polymorphic_on': type
    }


class CancerGeneListEntry(GeneListEntry):
    id = db.Column(
        db.Integer,
        db.ForeignKey('genelistentry.id'),
        primary_key=True
    )

    is_cancer_gene = db.Column(db.Boolean())

    __mapper_args__ = {
        'polymorphic_identity': 'cancer_entry',
    }


class GeneList(BioModel):
    name = db.Column(db.String(256), nullable=False, unique=True, index=True)
    entries = db.relationship('GeneListEntry')


class Kinase(BioModel):
    """Kinase represents an entity interacting with some site.

    The protein linked to a kinase is chosen as the `preferred_isoform` of a
    gene having the same name as given kinase (since we do not have specific
    refseq identificator for a single kinase).
    Not every kinase has an associated protein.
    """
    name = db.Column(db.String(80), unique=True, index=True)
    protein_id = db.Column(db.Integer, db.ForeignKey('protein.id'))
    group_id = db.Column(db.Integer, db.ForeignKey('kinasegroup.id'))

    def __repr__(self):
        return '<Kinase {0} belonging to {1} group>'.format(
            self.name,
            self.group
        )

    @property
    def mutations(self):
        if not self.protein:
            return []
        return self.protein.mutations.all()

    def to_json(self):
        return {
            'name': self.name,
            'protein': {
                'refseq': self.protein.refseq
            } if self.protein else None
        }


class KinaseGroup(BioModel):
    """Kinase group is the only grouping of kinases currently in use.

    The nomenclature may differ across sources and a `group` here
    may be equivalent to a `family` in some publications / datasets.
    """
    name = db.Column(db.String(80), unique=True, index=True)
    kinases = db.relationship(
        'Kinase',
        order_by='Kinase.name',
        backref='group'
    )

    def __repr__(self):
        return '<KinaseGroup {0}, with {1} kinases>'.format(
            self.name,
            len(self.kinases)
        )


class Gene(BioModel):
    """Gene is uniquely identified although has multiple protein isoforms.

    The isoforms are always located on the same chromosome, strand and are
    a product of the same gene. The major function of this model is to group
    isoforms classified as belonging to the same gene and to verify
    consistency of chromosomes and strands information across the database.
    """
    # HGNC symbols are allowed to be varchar(255) but 40 is still safe
    # as for storing symbols that are currently in use. Let's use 2 x 40.
    name = db.Column(db.String(80), unique=True, index=True)

    # TRUE represent positive (+) strand, FALSE represents negative (-) strand
    # As equivalent to (?) from Generic Feature Format NULL could be used.
    strand = db.Column(db.Boolean())

    # Chromosome - up to two digits (1-22 inclusive), X and Y and eventually MT
    chrom = db.Column(db.CHAR(2))

    isoforms = db.relationship(
        'Protein',
        backref='gene',
        foreign_keys='Protein.gene_id'
    )

    preferred_isoform_id = db.Column(
        db.Integer,
        db.ForeignKey('protein.id', name='fk_preferred_isoform')
    )
    preferred_isoform = db.relationship(
        'Protein',
        uselist=False,
        foreign_keys=preferred_isoform_id,
        post_update=True
    )

    @cached_property
    def alternative_isoforms(self):
        return [
            isoform
            for isoform in self.isoforms
            if isoform.id != self.preferred_isoform_id
        ]

    def __repr__(self):
        return '<Gene {0}, with {1} isoforms>'.format(
            self.name,
            len(self.isoforms)
        )

    def to_json(self):
        return {
            'name': self.name,
            'preferred_isoform': (
                self.preferred_isoform.refseq
                if self.preferred_isoform
                else None
            ),
            'isoforms_count': len(self.isoforms)
        }


class Pathway(BioModel):
    description = db.Column(db.Text)

    gene_ontology = db.Column(db.Integer)
    reactome = db.Column(db.Integer)

    genes = db.relationship(
        'Gene',
        secondary=make_association_table('pathway.id', 'gene.id'),
        backref='pathways'
    )

    @hybrid_property
    def gene_count(self):
        return len(self.genes)

    @gene_count.expression
    def gene_count(cls):

        from sqlalchemy.orm import aliased

        pathway_aliased = aliased(Pathway)

        return (
            db.session.query(Gene).
            join(pathway_aliased, Gene.pathways).
            filter(pathway_aliased.id == cls.id).
            statement.with_only_columns([func.count()]).order_by(None)
        )

    def to_json(self):
        return {
            'id': self.id,
            'description': self.description,
            'reactome': self.reactome,
            'gene_ontology': self.gene_ontology,
            'gene_count': self.gene_count,
            'genes': [
                gene.to_json()
                for gene in self.genes
            ]
        }


class Protein(BioModel):
    """Protein represents a single isoform of a product of given gene."""

    gene_id = db.Column(db.Integer, db.ForeignKey('gene.id', use_alter=True))
    gene_name = association_proxy('gene', 'name')

    # refseq id of mRNA sequence (always starts with 'NM_')
    # HGNC reserves up to 50 characters; 32 seems good enough but
    # I did not found any technical documentation; useful resource:
    # ncbi.nlm.nih.gov/books/NBK21091/
    refseq = db.Column(db.String(32), unique=True, index=True)

    # sequence of amino acids represented by one-letter IUPAC symbols
    sequence = db.Column(db.Text, default='')

    # sequence of ones and zeros where ones indicate disorder region
    # should be no longer than the sequence (defined above)
    disorder_map = db.Column(db.Text, default='')

    # transcription start/end coordinates
    tx_start = db.Column(db.Integer)
    tx_end = db.Column(db.Integer)

    # interactors count will be displayed in NetworkView
    interactors_count = db.Column(db.Integer)

    # coding sequence domain start/end coordinates
    cds_start = db.Column(db.Integer)
    cds_end = db.Column(db.Integer)

    sites = db.relationship(
        'Site',
        order_by='Site.position',
        backref='protein'
    )
    mutations = db.relationship(
        'Mutation',
        order_by='Mutation.position',
        lazy='dynamic',
        backref='protein'
    )
    domains = db.relationship(
        'Domain',
        backref='protein'
    )
    kinase = db.relationship(
        'Kinase',
        backref='protein'
    )

    def __init__(self, **kwargs):
        for key in ('sequence', 'disorder_map'):
            if key not in kwargs:
                kwargs[key] = ''

        super().__init__(**kwargs)

    def __repr__(self):
        return '<Protein {0} with seq of {1} aa from {2} gene>'.format(
            self.refseq,
            self.length,
            self.gene.name
        )

    @hybrid_property
    def has_ptm_mutations(self):
        # TODO
        raise NotImplementedError

    @has_ptm_mutations.expression
    def has_ptm_mutations(cls):
        return (
            select([
                case(
                    [(
                        exists()
                        .where(and_(
                            Site.protein_id == cls.id,
                            Mutation.protein_id == cls.id,
                            Site.position.between(
                                Mutation.position - 7,
                                Mutation.position + 7
                            )
                        )).correlate(cls),
                        True
                    )],
                    else_=False,
                ).label('has_ptm_mutations')
            ])
            .label('has_ptm_mutations_select')
        )

    @hybrid_property
    def mutations_count(self):
        return self.mutations.count()

    @mutations_count.expression
    def mutations_count(cls):
        return (
            select([func.count(Mutation.id)]).
            where(Mutation.protein_id == cls.id).
            label('mutations_count')
        )

    @hybrid_property
    def sites_count(self):
        return len(self.sites)

    @sites_count.expression
    def sites_count(cls):
        return (
            select([func.count(Site.id)]).
            where(Site.protein_id == cls.id).
            label('sites_count')
        )

    def to_json(self, data_filter=None):
        if not data_filter:
            data_filter = lambda x: x
            muts_count = self.mutations_count
        else:
            muts_count = len(data_filter(self.mutations))
        return {
            'gene_name': self.gene_name,
            'refseq': self.refseq,
            'sites_count': len(data_filter(self.sites)),
            'muts_count': muts_count,
            'ptm_muts': sum(
                1 for m in data_filter(self.mutations)
                if m.is_ptm()
            )
        }

    @cached_property
    def is_preferred_isoform(self):
        return self.gene.preferred_isoform == self

    @cached_property
    def length(self):
        """Length of protein's sequence"""
        return len(self.sequence)

    @cached_property
    def disorder_length(self):
        """How many residues are disordered."""
        return sum([int(residue) for residue in self.disorder_map])

    @cached_property
    def disorder_regions(self):
        """Transform binary disorder data into list of spans.

        Each span is represented by a tuple: (start, length).
        The coordinates are 1-based.
        """

        disorder_regions = []
        inside_region = False

        for i in range(len(self.disorder_map)):
            residue = int(self.disorder_map[i])
            if inside_region:
                if not residue:
                    inside_region = False
                    disorder_regions[-1][1] = i - disorder_regions[-1][0]
            else:
                if residue:
                    disorder_regions += [[i + 1, 1]]
                    inside_region = True

        return disorder_regions

    @hybrid_property
    def kinases(self):
        """Get all kinases associated with this protein"""
        kinases = set()
        # first of all we need kinases to be a foreign key to proteins
        for site in self.sites:
            kinases.update((site.kinases))
        return kinases

    @kinases.expression
    def kinases(self):
        """SQL expression for kinases"""
        q = select(Protein.sites.kinases).\
            distinct()
        return db.session.query(q)

    @hybrid_property
    def kinase_groups(self):
        """Get all kinase_groups associated with this protein"""
        kinase_groups = set()
        for site in self.sites:
            kinase_groups.update((site.kinase_groups))
        return kinase_groups

    def get_sites_from_range(self, left, right):
        """Retrieves sites from given range defined as <left, right>, inclusive.

        Algorithm is based on bisection and an assumption,
        that sites are sorted by position in the database.
        """
        assert left < right

        sites = self.sites

        for i, site in enumerate(sites):
            if site.position >= left:
                start = i
                break
        else:
            return []

        for i, site in enumerate(reversed(sites)):
            if site.position <= right:
                end = -i
                break
        else:
            return []

        return sites[start:end]

    def _calc_interactors_count(self):
        return len(self.kinases) + len(self.kinase_groups)


class Site(BioModel):
    position = db.Column(db.Integer, index=True)
    residue = db.Column(db.String(1))
    pmid = db.Column(db.Text)
    type = db.Column(db.Text)
    protein_id = db.Column(db.Integer, db.ForeignKey('protein.id'))
    kinases = db.relationship(
        'Kinase',
        secondary=make_association_table('site.id', 'kinase.id')
    )
    kinase_groups = db.relationship(
        'KinaseGroup',
        secondary=make_association_table('site.id', 'kinasegroup.id')
    )

    def __repr__(self):
        return '<Site of protein: {0}, at pos: {1}>'.format(
            Protein.query.get(self.protein_id).refseq,
            self.position
        )

    def to_json(self):
        return {
            'position': self.position,
            'type': self.type,
            'residue': self.residue
        }


class Cancer(BioModel):
    code = db.Column(db.String(16), unique=True)
    name = db.Column(db.String(64), unique=True)

    def __repr__(self):
        return '<Cancer with code: {0}, named: {1}>'.format(
            self.code,
            self.name
        )


class InterproDomain(BioModel):
    # Interpro ID
    accession = db.Column(db.Text)

    # Interpro Short Description
    short_description = db.Column(db.Text)

    # Interpro Description
    description = db.Column(db.Text)

    # is this a family? or domain? or maybe something else?
    type = db.Column(db.String(16))

    # How deep in the hierarchy this interpro domain is placed
    level = db.Column(db.Integer)

    # What is the interpro domain above in hierarchy tree
    parent_id = db.Column(db.Integer, db.ForeignKey('interprodomain.id'))

    # Relation with backref allowings easy tree traversal
    children = db.relationship(
        'InterproDomain',
        backref=db.backref('parent', remote_side='InterproDomain.id')
    )

    # Occurrences (annotations) of real biological domains
    occurrences = db.relationship('Domain', backref='interpro')

    def __repr__(self):
        return '<InterproDomain {0} of type {1} on {2} level "{3}" {4}>'.format(
            self.accession,
            self.type,
            self.level,
            self.short_description,
            'with parent: ' + str(self.parent.accession)
            if self.parent_id
            else ''
        )


class Domain(BioModel):
    protein_id = db.Column(db.Integer, db.ForeignKey('protein.id'))
    interpro_id = db.Column(db.Integer, db.ForeignKey('interprodomain.id'))

    start = db.Column(db.Integer)
    end = db.Column(db.Integer)

    def __len__(self):
        return self.end - self.start

    def __repr__(self):
        return '<Domain "{0}" in {1}, at [{2}, {3}] >'.format(
            self.interpro.accession,
            self.protein.refseq,
            self.start,
            self.end
        )


def mutation_details_relationship(class_name, use_list=False, **kwargs):
    return db.relationship(
        class_name,
        backref='mutation',
        uselist=use_list,
        **kwargs
    )


mutation_site_association = make_association_table('site.id', 'mutation.id')


class CancerMetaManager(UserList):

    def to_json(self, filter=lambda x: x):
        cancer_occurances = [
            datum.to_json()
            for datum in
            filter(self.data)
        ]

        return {'TCGA metadata': cancer_occurances}

    def summary(self, filter=lambda x: x):
        return [
            datum.summary()
            for datum in self.data
            # one could use:
            # datum.summary()
            # for datum in filter(self.data)
            # but I think that there is no need for that now
        ]

    def get_value(self, filter=lambda x: x):
        return sum(
            (
                data.get_value()
                for data in filter(self.data)
            )
        )


class MIMPMetaManager(UserList):

    @staticmethod
    def sort_by_probability(mimps):
        return mimps.sort(
            key=lambda mimp: mimp.probability,
            reverse=True
        )

    def to_json(self, filter=lambda x: x):

        gains = []
        losses = []

        for mimp in self.data:
            if mimp.effect:
                gains.append(mimp)
            else:
                losses.append(mimp)

        self.sort_by_probability(gains)
        self.sort_by_probability(losses)

        return {
            'gain': [mimp.to_json() for mimp in gains],
            'loss': [mimp.to_json() for mimp in losses],
            'effect': self.effect
        }

    @property
    def effect(self):
        effects = set()

        # TODO: sort by p-value, so first we will have loss
        # if the loss data is more reliable and vice versa.
        for mimp in self.data:
            if mimp.effect:
                effects.add('gain')
            else:
                effects.add('loss')

        return '/'.join(effects)

    @property
    def sites(self):
        return set(
            (
                mimp.site
                for mimp in self.data
            )
        )


class Mutation(BioModel):
    __table_args__ = (
        db.Index('mutation_index', 'alt', 'protein_id', 'position'),
        # TODO: is constraint neccessary?
        # db.UniqueConstraint('alt', 'protein_id', 'position')
    )

    position = db.Column(db.Integer)
    alt = db.Column(db.String(1))
    protein_id = db.Column(db.Integer, db.ForeignKey('protein.id'))

    # To speed up results retrieval one can precompute value of
    # 'is_ptm' property. It does not carry meaningful information
    # for novel mutations until correctly precomputed (e.g. with
    # instruction: m.precomputed_is_ptm = m.is_ptm).
    # You can distinguish if it was precomputed: check if the value
    # is different than None. Be careful with boolean evaluation!
    precomputed_is_ptm = db.Column(db.Boolean)

    meta_cancer = mutation_details_relationship(
        'CancerMutation',
        use_list=True,
        collection_class=CancerMetaManager
    )
    meta_inherited = mutation_details_relationship('InheritedMutation')
    meta_ESP6500 = mutation_details_relationship('ExomeSequencingMutation')
    meta_1KG = mutation_details_relationship('The1000GenomesMutation')

    meta_MIMP = mutation_details_relationship(
        'MIMPMutation',
        use_list=True,
        collection_class=MIMPMetaManager
    )

    # one mutation can affect multiple sites and
    # one site can be affected by multiple mutations
    sites = db.relationship(
        'Site',
        secondary=mutation_site_association
    )

    # mapping: source name -> column name
    source_fields = OrderedDict(
        (
            ('TCGA', 'meta_cancer'),
            ('ClinVar', 'meta_inherited'),
            ('ESP6500', 'meta_ESP6500'),
            ('1KGenomes', 'meta_1KG'),
        )
    )

    populations_1KG = association_proxy(
        'meta_1KG',
        'affected_populations'
    )
    populations_ESP6500 = association_proxy(
        'meta_ESP6500',
        'affected_populations'
    )

    # affects_1KG = association_proxy('meta_1KG', 'affects')
    cancer_code = association_proxy('meta_cancer', 'cancer_code')
    sig_code = association_proxy('meta_inherited', 'sig_code')

    def get_source_name(self, column_name):
        return {v: k for k, v in self.source_fields.items()}.get(
            column_name,
            'other'
        )

    def __repr__(self):
        return '<Mutation in {0}, at {1} aa, substitution to: {2}>'.format(
            self.protein.refseq,
            self.position,
            self.alt
        )

    @hybrid_property
    def sources_dict(self):
        """Return list of names of sources which mention this mutation

        Names of sources are determined by source_fields class property.
        """
        sources = {}
        for source_name, associated_field in self.source_fields.items():
            field_value = getattr(self, associated_field)
            if field_value:
                sources[source_name] = field_value
        return sources

    @hybrid_property
    def sources(self):
        """Return list of names of sources which mention this mutation

        Names of sources are determined by source_fields class property.
        """
        sources = []
        for source_name, associated_field in self.source_fields.items():
            if getattr(self, associated_field):
                sources.append(source_name)
        return sources

    @hybrid_property
    def is_confirmed(self):
        """Mutation is confirmed if there are metadata from one of four studies

        (or experiments). Presence of MIMP metadata does not imply
        if mutation has been ever studied experimentally before.
        """
        return any([
            getattr(self, field)
            for field in self.source_fields.values()
        ])

    @is_confirmed.expression
    def is_confirmed(self):
        """SQL expression for is_confirmed"""
        return or_(
            relationship_field.any()
            if relationship_field.prop.uselist
            else relationship_field.has()
            for relationship_field in map(
                lambda field: getattr(self, field),
                self.source_fields.values()
            )
        )

    @cached_property
    def all_metadata(self):
        return {
            self.get_source_name(key): value.to_json()
            for key, value in self.__dict__.items()
            if key.startswith('meta_') and value
        }

    @hybrid_method
    def is_ptm(self, filter_manager=None):
        """Mutation is PTM related if it may affect PTM site.

        Mutations flanking PTM site in a distance up to 7 residues
        from a site (called here 'distal') will be included too.

        This method works very similarly to is_ptm_distal property.
        """
        sites = self.protein.sites
        if filter_manager:
            sites = filter_manager.apply(sites)
        print(self.is_close_to_some_site(7, 7, sites))
        return self.is_close_to_some_site(7, 7, sites)

    @hybrid_property
    def ref(self):
        sequence = self.protein.sequence
        return sequence[self.position - 1]

    @hybrid_property
    def is_ptm_direct(self):
        """True if the mutation is on the same position as some PTM site."""
        return self.is_close_to_some_site(0, 0)

    @hybrid_property
    def is_ptm_proximal(self):
        """Check if the mutation is in close proximity of some PTM site.

        Proximity is defined here as [pos - 3, pos + 3] span,
        where pos is the position of a PTM site.
        """
        return self.is_close_to_some_site(3, 3)

    @hybrid_property
    def is_ptm_distal(self):
        """Check if the mutation is distal flanking mutation of some PTM site.

        Distal flank is defined here as [pos - 7, pos + 7] span,
        where pos is the position of a PTM site.
        """
        # if we have precomputed True or False (i.e. it's known
        # mutations - so we precomputed this) then return this
        if self.precomputed_is_ptm is not None:
            return self.precomputed_is_ptm
        # otherwise it's a novel mutation - let's check proximity
        return self.is_close_to_some_site(7, 7)

    def get_affected_ptm_sites(self, site_filter=lambda x: x):
        """Get PTM sites that might be affected by this mutation,

        when taking into account -7 to +7 spans of each PTM site.
        """
        sites = site_filter(self.protein.sites)
        pos = self.position
        a = 0
        b = len(sites)
        sites_affected = []

        hit = None

        while a != b:
            pivot = (b - a) // 2 + a
            site_pos = sites[pivot].position
            if site_pos - 7 <= pos and pos <= site_pos + 7:
                hit = pivot
                sites_affected.append(sites[pivot])
                break
            if pos > site_pos:
                a = pivot + 1
            else:
                b = pivot
        else:
            return []

        def cond():
            try:
                site_pos = sites[pivot].position
                return site_pos - 7 <= pos and pos <= site_pos + 7
            except IndexError:
                return []

        # go to right from found site, check if there is more overlapping sites
        pivot = hit + 1
        while cond():
            sites_affected.append(sites[pivot])
            pivot += 1

        # and then go to the left
        pivot = hit - 1
        while cond():
            sites_affected.append(sites[pivot])
            pivot -= 1

        return sites_affected

    def impact_on_specific_ptm(self, site):
        if self.position == site.position:
            return 'direct'
        elif self.meta_MIMP:
            return 'network-rewiring'
        elif abs(self.position - site.position) < 4:
            return 'proximal'
        elif abs(self.position - site.position) < 8:
            return 'distal'
        else:
            return 'none'

    def impact_on_ptm(self, site_filter=lambda x: x):
        """How intense might be an impact of the mutation on a PTM site.

        It describes impact on the closest PTM site or on a site chosen by
        MIMP algorithm (so it applies only when 'network-rewiring' is returned)
        """
        sites = site_filter(self.protein.sites)

        if self.is_close_to_some_site(0, 0, sites):
            return 'direct'
        elif any(site in sites for site in self.meta_MIMP.sites):
            return 'network-rewiring'
        elif self.is_close_to_some_site(3, 3, sites):
            return 'proximal'
        elif self.is_close_to_some_site(7, 7, sites):
            return 'distal'
        return 'none'

    def find_closest_sites(self, distance=7, site_filter=lambda x: x):
        pos = self.position

        sites = {
            site.position: site
            for site in site_filter(
                Site.query.filter(
                    and_(
                        Site.protein_id == self.protein_id,
                        Site.position.between(pos - distance, pos + distance)
                    )
                ).all()
            )
        }

        found_sites = set()

        for i in range(distance + 1):
            if pos + i in sites:
                found_sites.add(sites[pos + i])
            if pos - i in sites:
                found_sites.add(sites[pos - i])
            if found_sites:
                break

        return found_sites

    @hybrid_method
    def is_close_to_some_site(self, left, right, sites=None):
        """Check if the mutation lies close to any of sites.

        Arguments define span around each site to be checked:
        (site_pos - left, site_pos + right)
        site_pos is the position of a site

        Algorithm is based on bisection and an assumption,
        that sites are sorted by position in the database.
        """
        if sites is None:
            sites = self.protein.sites
        pos = self.position
        a = 0
        b = len(sites)
        while a != b:
            p = (b - a) // 2 + a
            site_pos = sites[p].position
            if site_pos - left <= pos and pos <= site_pos + right:
                return True
            if pos > site_pos:
                a = p + 1
            else:
                b = p
        return False

    @is_close_to_some_site.expression
    def is_close_to_some_site(self, left, right):
        """SQL expression for is_close_to_some_site"""
        position = self.position
        q = exists().where(
            and_(
                Site.protein_id == self.protein_id,
                Site.position.between(position - left, position + right)
            )
        )
        return db.session.query(q).scalar()


class MutationDetails:
    """Base for tables defining detailed metadata for specific mutations"""

    @declared_attr
    def mutation_id(cls):
        return db.Column(db.Integer, db.ForeignKey('mutation.id'))

    def get_value(self, filter=lambda x: x):
        """Return number representing value to be used in needleplot"""
        raise NotImplementedError

    def to_json(self, filter=lambda x: x):
        """Return JSON serializable representation for needleplot tooltips.

        The default serializer is: json.JSONEncoder
            https://docs.python.org/3/library/json.html#json.JSONEncoder
        """
        raise NotImplementedError

    def summary(self, filter=lambda x: x):
        """Return short JSON serializable representation of the mutation"""
        raise NotImplementedError


class CancerMutation(MutationDetails, BioModel):
    """Metadata for cancer mutations from ICGC data portal"""
    sample_name = db.Column(db.String(64))
    cancer_id = db.Column(db.Integer, db.ForeignKey('cancer.id'))
    cancer = db.relationship('Cancer')

    count = db.Column(db.Integer)

    def get_value(self, filter=lambda x: x):
        return self.count

    def to_json(self):
        return {
            'Cancer': self.cancer.name,
            'Value': self.count
        }

    def summary(self, filter=lambda x: x):
        return self.cancer_code

    cancer_code = association_proxy('cancer', 'code')


class InheritedMutation(MutationDetails, BioModel):
    """Metadata for inherited diseased mutations from ClinVar from NCBI

    Columns description come from source VCF file headers.
    """

    # RS: dbSNP ID (i.e. rs number)
    db_snp_id = db.Column(db.Integer)

    # MUT: Is mutation (journal citation, explicit fact):
    # a low frequency variation that is cited
    # in journal and other reputable sources
    is_low_freq_variation = db.Column(db.Boolean)

    # VLD: This bit is set if the variant has 2+ minor allele
    # count based on frequency or genotype data
    is_validated = db.Column(db.Boolean)

    # PMC: Links exist to PubMed Central article
    is_in_pubmed_central = db.Column(db.Boolean)

    clin_data = db.relationship(
        'ClinicalData',
        primaryjoin='foreign(InheritedMutation.id)==ClinicalData.inherited_id',
        uselist=True
    )

    sig_code = association_proxy('clin_data', 'sig_code')

    def get_value(self, filter=lambda x: x):
        return len(filter(self.clin_data))

    def to_json(self, filter=lambda x: x):
        return {
            'dbSNP id': 'rs' + str(self.db_snp_id),
            'Is validated': bool(self.is_validated),
            'Is low frequency variation': bool(self.is_low_freq_variation),
            'Is in PubMed Central': bool(self.is_in_pubmed_central),
            'Clinical': [
                d.to_json()
                for d in filter(self.clin_data)
            ]
        }

    def summary(self, filter=lambda x: x):
        return [
            d.disease_name
            for d in filter(self.clin_data)
        ]


class ClinicalData(BioModel):

    inherited_id = db.Column(db.Integer, db.ForeignKey('inheritedmutation.id'))

    significance_codes = {
        '0': 'Uncertain significance',
        '1': 'Not provided',
        '2': 'Benign',
        '3': 'Likely benign',
        '4': 'Likely pathogenic',
        '5': 'Pathogenic',
        '6': 'Drug response',
        '7': 'Histocompatibility',
        '255': 'Other'
    }

    # CLNSIG: Variant Clinical Significance:
    sig_code = db.Column(db.Text)   # TODO: make it int

    # CLNDBN: Variant disease name
    disease_name = db.Column(db.Text)

    @property
    def significance(self):
        return self.significance_codes.get(self.sig_code, None)

    # CLNREVSTAT: ?
    # no_assertion - No assertion provided,
    # no_criteria - No assertion criteria provided,
    # single - Criteria provided single submitter,
    # mult - Criteria provided multiple submitters no conflicts,
    # conf - Criteria provided conflicting interpretations,
    # exp - Reviewed by expert panel,
    # guideline - Practice guideline
    rev_status = db.Column(db.Text)

    def to_json(self, filter=lambda x: x):
        return {
            'Disease': self.disease_name,
            'Significane': self.significance,
            'Review status': self.rev_status
        }


class PopulationMutation(MutationDetails):
    """Metadata common for mutations from all population-wide studies

    MAF:
        All - total value
    """
    populations = {
        # place mappings here: field name -> population name
    }

    maf_all = db.Column(db.Float)

    def get_value(self, filter=lambda x: x):
        return self.maf_all

    def summary(self, filter=lambda x: x):
        return self.get_value()

    @hybrid_property
    def affected_populations(self):
        return [
            field
            for field in self.populations.keys()
            if getattr(self, field)
        ]


class ExomeSequencingMutation(PopulationMutation, BioModel):
    """Metadata for ESP 6500 mutation

    MAF:
        EA - European American
        AA - African American
    """
    populations = OrderedDict(
        (
            ('maf_ea', 'European American'),
            ('maf_aa', 'African American')
        )
    )

    maf_ea = db.Column(db.Float)
    maf_aa = db.Column(db.Float)

    def to_json(self, filter=lambda x: x):
        return {
            'MAF': self.maf_all,
            'MAF EA': self.maf_ea,
            'MAF AA': self.maf_aa,
        }


class The1000GenomesMutation(PopulationMutation, BioModel):
    """Metadata for 1 KG mutation"""
    maf_eas = db.Column(db.Float)
    maf_amr = db.Column(db.Float)
    maf_afr = db.Column(db.Float)
    maf_eur = db.Column(db.Float)
    maf_sas = db.Column(db.Float)

    # note: those are defined as super populations by 1000 Genomes project
    populations = OrderedDict(
        (
            ('maf_eas', 'East Asian'),
            ('maf_amr', 'Ad Mixed American'),
            ('maf_afr', 'African'),
            ('maf_eur', 'European'),
            ('maf_sas', 'South Asian')
        )
    )

    def to_json(self, filter=lambda x: x):
        return {
            'MAF': self.maf_all,
            'MAF EAS': self.maf_eas,
            'MAF AMR': self.maf_amr,
            'MAF AFR': self.maf_afr,
            'MAF EUR': self.maf_eur,
            'MAF SAS': self.maf_sas,
        }

    """
    @hybrid_method
    def affects(self, population_name):
        return getattr(self, population_name) > 0
    """


class MIMPMutation(MutationDetails, BioModel):
    """Metadata for MIMP mutation"""
    site_id = db.Column(db.Integer, db.ForeignKey('site.id'))
    site = db.relationship('Site')

    probability = db.Column(db.Float)

    pwm = db.Column(db.Text)
    pwm_family = db.Column(db.Text)

    # gain = +1, loss = -1
    effect = db.Column(db.Boolean)

    # position of a mutation in an associated motif
    position_in_motif = db.Column(db.Integer)

    def to_json(self, filter=lambda x: x):
        return {
            'effect': 'gain' if self.effect else 'loss',
            'pwm': self.pwm,
            'pos_in_motif': self.position_in_motif,
            'pwm_family': self.pwm_family,
            'site': self.site.to_json(),
            'probability': self.probability
        }

    def __repr__(self):
        return '<MIMPMutation %s>' % self.id