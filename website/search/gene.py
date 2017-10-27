from abc import ABC, abstractmethod
from collections import defaultdict

from Levenshtein import distance
from sqlalchemy import and_
from werkzeug.utils import cached_property

from models import Protein, UniprotEntry, ProteinReferences
from models import Gene
from database import db


class GeneMatch:

    def __init__(self, gene=None, scored_matches=None, matched_isoforms=None):
        self.gene = gene
        self.matches = scored_matches or {}
        self.matched_isoforms = matched_isoforms or []

    @classmethod
    def from_feature(cls, gene, matched_feature, match_score, matched_isoforms=None):
        return cls(gene, {matched_feature: match_score}, matched_isoforms)

    @property
    def best_score(self):
        """Score is based on edit distance. Less is better."""
        return min(self.matches.values())

    def __iadd__(self, other):
        if not self.gene:
            self.gene = other.gene

        assert self.gene == other.gene

        for feature, score in other.matches.items():
            if feature in self.matches:
                self.matches[feature] = min(self.matches[feature], score)
            else:
                self.matches[feature] = score

        self.matched_isoforms.extend(other.matched_isoforms)

        return self

    def __getattr__(self, key):
        return getattr(self.gene, key)


class GeneOrProteinSearch(ABC):

    def __init__(self, options=None):
        self.options = options

    @property
    @abstractmethod
    def name(self):
        """Internal name; also a base for pretty name shown to the user."""
        pass

    @cached_property
    def pretty_name(self):
        return self.name.replace('_', ' ').title()

    @abstractmethod
    def search(self, phrase, sql_filters=None, limit=None):
        pass

    @property
    def base_query(self):
        return Gene.query

    @property
    def query(self):
        query = self.base_query
        if self.options:
            query = query.options(self.options)
        return query


class GeneSearch(GeneOrProteinSearch):

    @property
    @abstractmethod
    def feature(self):
        """Name of the feature analysed by this GeneSearch."""
        return ''

    def get_feature(self, gene):
        return getattr(gene, self.feature)

    def search(self, phrase, sql_filters=None, limit=None):
        """Perform look up for a gene using provided phrase.

        The default implementation uses `get_feature`
        to perform search using the defined feature.

        If isoform-level filters are applied, these will
        be executed on the preferred_isoform of gene.
        """

        feature = self.get_feature(Gene)
        filters = [feature.like(phrase.strip() + '%')]

        if sql_filters:
            filters += sql_filters

        orm_query = (
            self.query
                .join(Protein, Gene.preferred_isoform)   # to allow PTM filter
                .filter(and_(*filters))
        )

        if limit:
            orm_query = orm_query.limit(limit)

        return [
            GeneMatch.from_feature(gene, self, self.sort_key(gene, phrase))
            for gene in orm_query
        ]

    def sort_key(self, gene, phrase):
        return distance(self.get_feature(gene), phrase)


class SymbolGeneSearch(GeneSearch):
    """Look up a gene by HGNC symbol

    Targets: Gene.name
    Example:
        search for "TP53" should return TP53 (among others)
    """

    name = 'gene_symbol'
    feature = 'name'


class GeneNameSearch(GeneSearch):
    """Look up a gene by full name, defined by HGNC

    Targets: Gene.full_name
    Example:
        search for "tumour protein" should return TP53 (among others)
    """

    name = 'gene_name'
    feature = 'full_name'


class ProteinSearch(GeneOrProteinSearch):
    """Looks up a gene, based on a feature of its isoforms.

    The matched isoforms are recorded in GeneMatch object.
    """

    def create_query(
            self, limit, filters, sql_filters, entities=(Gene, Protein),
            add_joins=lambda query: query
    ):

        if sql_filters:
            filters += sql_filters

        genes = (
            add_joins(
                self.query
                .join(Protein, Gene.isoforms)
            )
            .filter(and_(*filters))
            .group_by(Gene)
        )

        if limit:
            genes = genes.limit(limit)

        genes = genes.subquery('genes')

        query = (
            add_joins(
                db.session.query(*entities)
                .select_from(Gene)
                .join(Protein, Gene.isoforms)
            )
            .filter(and_(*filters))
            .filter(Gene.id == genes.c.id)
        )
        if self.options:
            query = query.options(self.options)
        return query

    @staticmethod
    @abstractmethod
    def sort_key(result, phrase):
        pass

    def parse_matches(self, query, phrase):
        matches = []
        # aggregate by genes
        isoforms_by_gene = defaultdict(set)
        for gene, isoform in query:
            isoforms_by_gene[gene].add(isoform)

        for gene, isoforms in isoforms_by_gene.items():

            match = GeneMatch.from_feature(
                gene,
                self,
                self.best_score(isoforms, phrase),
                matched_isoforms=isoforms
            )
            matches.append(match)

        return matches

    def best_score(self, results, phrase):
        return min(
            self.sort_key(isoform, phrase)
            for isoform in results
        )


class ProteinNameSearch(ProteinSearch):
    name = 'protein_name'

    def search(self, phrase, sql_filters=None, limit=None):

        filters = [Protein.full_name.ilike(phrase + '%')]

        query = self.create_query(limit, filters, sql_filters)

        return self.parse_matches(query, phrase)

    @staticmethod
    def sort_key(isoform, phrase):
        return distance(isoform.full_name, phrase)


class RefseqGeneSearch(ProteinSearch):
    """Look up a gene by isoforms RefSeq.

    Only numeric phrases and phrases starting with:
    "NM_" or "nm_" will be evaluated.

    Targets: Protein.refseq
    Example:
        search for "NM_00054" should return: TP53 [with matched
        isoforms = Protein(refseq=NM_000546)] (among others)
    """

    name = 'refseq'
    pretty_name = 'RefSeq'

    def search(self, phrase, sql_filters=None, limit=None):

        if phrase.isnumeric():
            phrase = 'NM_' + phrase

        if not (phrase.startswith('NM_') or phrase.startswith('nm_')):
            return []

        filters = [Protein.refseq.like(phrase + '%')]

        query = self.create_query(limit, filters, sql_filters)

        return self.parse_matches(query, phrase)

    @staticmethod
    def sort_key(isoform, phrase):
        return distance(isoform.refseq, phrase)


class SummarySearch(ProteinSearch):
    """Look up a gene by summary of isoforms.

    This is full-text search and may be expensive.

    Targets: Protein.summary
    """

    name = 'summary'

    def __init__(self, options=None, minimal_length=3):
        super().__init__(options)
        self.minimal_length = minimal_length

    def search(self, phrase, sql_filters=None, limit=None):

        if len(phrase) < self.minimal_length:
            return []

        filters = [Protein.summary.ilike('%' + phrase + '%')]

        query = self.create_query(limit, filters, sql_filters)

        return self.parse_matches(query, phrase)

    @staticmethod
    def sort_key(isoform, phrase):
        return distance(isoform.summary, phrase)


class UniprotSearch(ProteinSearch):
    """Look up a gene by isoforms Uniprot accession.

    Only phrases longer than 2 characters will be evaluated.

    Targets: Protein.external_references.uniprot_entries
    """

    name = 'uniprot'

    def search(self, phrase, sql_filters=None, limit=None):

        if len(phrase) < 3:
            return []

        filters = [UniprotEntry.accession.like(phrase + '%')]

        def add_joins(q):
            return q.join(ProteinReferences).join(UniprotEntry)

        query = self.create_query(
            limit, filters, sql_filters,
            (Gene, Protein, UniprotEntry), add_joins
        )

        return self.parse_matches(query, phrase)

    def parse_matches(self, query, phrase):

        matches = []

        # aggregate by genes
        results_by_gene = defaultdict(set)

        for gene, isoform, uniprot in query:
            results_by_gene[gene].add((isoform, uniprot))

        for gene, results in results_by_gene.items():
            isoforms, uniprot_entries = zip(*results)

            match = GeneMatch.from_feature(
                gene,
                self,
                self.best_score(uniprot_entries, phrase),
                matched_isoforms=isoforms
            )
            matches.append(match)

        return matches

    @staticmethod
    def sort_key(uniprot, phrase):
        return distance(uniprot.accession, phrase)


search_feature_engines = [
    RefseqGeneSearch,
    SymbolGeneSearch,
    GeneNameSearch,
    ProteinNameSearch,
    UniprotSearch,
    SummarySearch
]

