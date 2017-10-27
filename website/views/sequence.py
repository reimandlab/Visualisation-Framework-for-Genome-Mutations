from operator import attrgetter

from flask import jsonify
from flask import redirect
from flask import render_template as template
from flask import request
from flask import url_for
from flask_login import current_user
from sqlalchemy import and_

from helpers.tracks import DomainsTrack
from helpers.tracks import MutationsTrack
from helpers.tracks import SequenceTrack
from helpers.tracks import Track
from helpers.tracks import TrackElement
from models import Domain
from models import Mutation
from models import Site
from views.abstract_protein import AbstractProteinView, GracefulFilterManager, ProteinRepresentation
from ._commons import represent_mutation
from .filters import common_filters, ProteinFiltersData
from .filters import create_widgets


def prepare_tracks(protein, raw_mutations):

    disorder = [
        TrackElement(*region) for region in protein.disorder_regions
    ]
    tracks = [
        SequenceTrack(protein),
        Track('disorder', disorder),
        DomainsTrack(
            Domain.query.filter(
                and_(
                    Domain.protein == protein,
                    Domain.interpro.has(type='Domain')
                )
            )
        ),
        MutationsTrack(raw_mutations)
    ]
    return tracks


def prepare_sites(sites):
    return [
        {
            'start': site.position - 7,
            'end': site.position + 7,
            'type': str(site.type)
        } for site in sites
    ]


class SequenceRepresentation(ProteinRepresentation):

    def __init__(self, protein, filter_manager, include_kinases_from_groups=False):
        super().__init__(protein, filter_manager, include_kinases_from_groups)

        sites, kinases, kinase_groups = self.get_sites_and_kinases(only_sites_with_kinases=False)

        tracks = prepare_tracks(protein, self.protein_mutations)

        source = filter_manager.get_value('Mutation.sources')
        source_model = Mutation.get_source_model(source)
        value_type = source_model.value_type

        parsed_mutations = self.represent_needles()

        self.json_data = {
            'value_type': value_type,
            'log_scale': (value_type == 'frequency'),
            'mutations': parsed_mutations,
            'sites': prepare_sites(sites),
            'tracks': tracks
        }

    def represent_needles(self):

        source_name = self.filter_manager.get_value('Mutation.sources')

        get_source_data = attrgetter(Mutation.source_fields[source_name])

        get_mimp_data = attrgetter('meta_MIMP')

        data_filter = self.filter_manager.apply

        response = []

        for mutation in self.protein_mutations:

            needle = represent_mutation(mutation, data_filter)

            field = get_source_data(mutation)
            metadata = {
                source_name: field.to_json(data_filter)
            }

            mimp = get_mimp_data(mutation)

            if mimp:
                metadata['MIMP'] = mimp.to_json()

            needle['summary'] = field.summary(data_filter)
            needle['value'] = field.get_value(data_filter)
            needle['meta'] = metadata
            needle['category'] = mutation.impact_on_ptm(data_filter)

            response.append(needle)

        return response


class SequenceViewFilters(GracefulFilterManager):

    def __init__(self, protein, **kwargs):

        filters = common_filters(protein, **kwargs)
        super().__init__(filters)
        self.update_from_request(request)


class SequenceView(AbstractProteinView):
    """Single protein view: includes needleplot and sequence"""

    filter_class = SequenceViewFilters

    def index(self):
        """Show SearchView as default page"""
        return redirect(url_for('SearchView:default', target='proteins'))

    def representation_data(self, refseq):

        protein, filter_manager = self.get_protein_and_manager(refseq)

        data = SequenceRepresentation(protein, filter_manager).as_json()

        data['mutation_table'] = template(
            'protein/mutation_table.html',
            mutations=data['mutations'],
            filters=filter_manager,
            protein=protein,
            value_type=data['value_type']
        )
        data['tracks'] = template(
            'protein/tracks.html',
            tracks=data['tracks']
        )

        response = {
            'content': data,
            'filters': ProteinFiltersData(filter_manager, protein).to_json()
        }

        return jsonify(response)

    def show(self, refseq):
        """Show a protein by:

        + needleplot
        + tracks (sequence + data tracks)
        """

        protein, filter_manager = self.get_protein_and_manager(refseq)

        user_datasets = current_user.datasets_names_by_uri()

        return template(
            'protein/show.html',
            protein=protein,
            filters=filter_manager,
            widgets=create_widgets(
                protein,
                filter_manager.filters,
                custom_datasets_names=user_datasets.values()
            ),
            site_types=['multi_ptm'] + Site.types,
            mutation_types=Mutation.types,
        )
