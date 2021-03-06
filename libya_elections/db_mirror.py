import logging

from django.forms import model_to_dict

from libya_elections.db_utils import BatchOperations


logger = logging.getLogger(__name__)


class MirrorStats(object):
    def __init__(self):
        self.unchanged_count = 0
        self.modified_record_count = 0
        self.new_record_count = 0
        self.not_there_anymore_count = 0
        self.missing_pks = []


def mirror_database(
        from_model,
        to_model,
        from_db_name='default',
        to_db_name='default'):
    """
    Given two tables with the same schema, possibly in different databases,
    update the second efficiently to contain the same data as
    the first, except that deleting records from the second table
    is optional (pass delete_missing_records=True to confirm you
    want that).

    Returns a MirrorStats object with some statistics about what happened.

    :param from_model, to_model: The Django models to copy from and to.
    :param from_db_name: Name of the DATABASES key of the database we're
    copying from. Default: 'default'.
    :param to_db_name: Name of the DATABASES key of the database we're
    copying to. Default: 'default'.
    """

    # NOW comes the fun part. We'll go through our two tables in parallel
    # in primary key order, so we can delete records from the target that no longer
    # exist in the source, and add or update records that have been
    # added or updated.
    from_queryset = from_model.objects.using(from_db_name).order_by('pk').iterator()
    to_queryset = to_model.objects.using(to_db_name).order_by('pk').iterator()

    def next_from():
        """Return next record in the table we're copying from, or None"""
        try:
            return from_queryset.next()
        except StopIteration:
            return None

    def next_to():
        """Return next record in the table we're copying to, or None"""
        try:
            return to_queryset.next()
        except StopIteration:
            return None

    stats = MirrorStats()

    # We'll do our adds and deletes in bulk.
    # Too bad there's no bulk_update, but updates should be the
    # least frequent operation.
    batch = BatchOperations(to_model)

    from_record = next_from()
    to_record = next_to()
    while from_record and to_record:
        if from_record.pk == to_record.pk:
            # Same record - if the data has changed, update our mirror
            # Note that if a record was flagged 'missing' in a previous
            # import and turns up again in a later dump, this update
            # will turn off the missing field for us.
            if model_to_dict(from_record) != model_to_dict(to_record):
                stats.modified_record_count += 1
                to_model.objects.filter(pk=from_record.pk)\
                    .update(**model_to_dict(from_record))
            else:
                stats.unchanged_count += 1
            # We've dealt with both of these records, move on in both tables
            from_record = next_from()
            to_record = next_to()
        elif from_record.pk < to_record.pk:
            # The "TO" table is missing a record that the FROM table has, so add it
            stats.new_record_count += 1
            batch.add(model_to_dict(from_record))
            # We've dealt with this from_record, on to the next
            from_record = next_from()
        else:  # from_record.pk > to_record.pk:
            # The "TO" table has a record that's not in the from table, so remove it
            # if delete_missing_records is set
            stats.not_there_anymore_count += 1
            stats.missing_pks.append(to_record.pk)
            # We've dealt with this to_record, on to the next
            to_record = next_to()

    # At this point, we might have left over records from one table
    # or the other (though not both)
    while from_record:
        # Records that aren't in the "TO" table and need to be added
        stats.new_record_count += 1
        batch.add(model_to_dict(from_record))
        from_record = next_from()
    while to_record:
        # Need to remove records from the "TO" table
        stats.not_there_anymore_count += 1
        stats.missing_pks.append(to_record.pk)
        to_record = next_to()

    # Finish out the batches if needed
    batch.flush()

    # Return statistics
    return stats
