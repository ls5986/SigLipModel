"""Conservative image relevance defaults; humans can override similarity selection."""
EXCLUDED = {'shared_amenity','floor_plan','unrelated'}

def selection(context, review=None):
    review = review or {}
    override = review.get('include_in_similarity')
    if type(override) is bool:
        return {'included':override,'source':'human','reason':'Your photo selection'}
    excluded = context in EXCLUDED
    return {'included':not excluded,'source':'automatic',
            'reason':'Excluded: '+context.replace('_',' ') if excluded else 'Relevant or uncertain property photo'}
