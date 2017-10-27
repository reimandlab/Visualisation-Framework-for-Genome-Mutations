from flask import request
from flask_classful import FlaskView
from flask import jsonify

from database import bdb
from models import Mutation
from helpers.filters import FilterManager
from .filters import common_filters
from ._commons import represent_mutation
from operator import attrgetter
from collections import OrderedDict


def represent_mutations(mutations, filter_manager):

    source_name = filter_manager.get_value('Mutation.sources')

    if source_name:
        get_source_data = attrgetter(Mutation.source_fields[source_name])

    get_mimp_data = attrgetter('meta_MIMP')

    data_filter = filter_manager.apply

    response = []

    for mutation in mutations:

        needle = represent_mutation(
            mutation,
            data_filter,
            representation_type=OrderedDict
        )

        needle['protein'] = mutation.protein.refseq
        needle['gene'] = mutation.protein.gene.name

        # place protein refseq id on the beginning
        needle.move_to_end('protein', last=False)
        needle.move_to_end('gene', last=False)

        needle['ptm_impact'] = mutation.impact_on_ptm(data_filter)

        if source_name:
            field = get_source_data(mutation)
            metadata = {
                source_name: field.to_json(data_filter)
            }
            needle['summary'] = field.summary()
            needle['value'] = field.get_value(data_filter)

        else:
            metadata = {
                name: field.to_json(data_filter)
                for name, field in mutation.sources_dict.items()
            }

        mimp = get_mimp_data(mutation)

        if mimp:
            metadata['MIMP'] = mimp.to_json()

        closest_sites = mutation.find_closest_sites(
            site_filter=data_filter
        )
        needle['closest_sites'] = [
            '%s %s' % (site.position, site.residue)
            for site in closest_sites
        ]

        needle.move_to_end('sites')

        needle['in_datasets'] = metadata

        response.append(needle)

    return response


class ChromosomeView(FlaskView):

    @staticmethod
    def _make_filters():
        filters = common_filters(None, default_source=None, source_nullable=False)
        filter_manager = FilterManager(filters)
        filter_manager.update_from_request(request)
        return filters, filter_manager

    def mutation(self, chrom, dna_pos, dna_ref, dna_alt):
        """Rest API endpoint.
        Stop codon mutations are not considered."""

        _, filter_manager = self._make_filters()

        if chrom.startswith('chr'):
            chrom = chrom[3:]

        items = bdb.get_genomic_muts(chrom, dna_pos, dna_ref, dna_alt)

        raw_mutations = filter_manager.apply([
            item['mutation'] for
            item in items
        ])

        parsed_mutations = represent_mutations(
            raw_mutations, filter_manager
        )

        return jsonify(parsed_mutations)
