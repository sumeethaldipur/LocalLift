from locallift.sources import ticketmaster
from locallift import db, ingest, developments

class Response:
    def __init__(self, body): self.body = body
    def json(self): return self.body

def test_ticketmaster_fetches_past_three_pages(monkeypatch):
    monkeypatch.setattr(ticketmaster.config.settings, 'ticketmaster_key', 'test')
    calls = []
    def fetch(*args, **kwargs):
        params = kwargs['params'].copy(); calls.append(params)
        return Response({'page': {'totalPages': 5}, '_embedded': {'events': [
            {'id': str(params['page']), 'name': 'Sample', 'dates': {'start': {'dateTime': '2026-10-29T20:00:00Z'}}}]}})
    monkeypatch.setattr(ticketmaster, 'request', fetch)
    result = ticketmaster.discover(40.7, -74, 1.5, 14)
    assert len(result['events']) == 5 and not result['truncated']
    assert len(calls) == 5 and all(c['radius'] == 2 for c in calls)

def test_ticketmaster_splits_ranges_and_deduplicates_boundary(monkeypatch):
    monkeypatch.setattr(ticketmaster.config.settings, 'ticketmaster_key', 'test')
    calls = []
    def fetch(*args, **kwargs):
        calls.append(kwargs['params'].copy())
        if len(calls) == 1: return Response({'page': {'totalPages': 11}})
        return Response({'page': {'totalPages': 1}, '_embedded': {'events': [{'id': 'same', 'name': 'Boundary event'}]}})
    monkeypatch.setattr(ticketmaster, 'request', fetch)
    result = ticketmaster.discover(40.7, -74, 2, 14)
    assert len(result['events']) == 1 and not result['truncated']
    assert calls[1]['endDateTime'] == calls[2]['startDateTime']

def test_ticketmaster_reports_budget_exhaustion(monkeypatch):
    monkeypatch.setattr(ticketmaster.config.settings, 'ticketmaster_key', 'test')
    monkeypatch.setattr(ticketmaster, 'MAX_REQUESTS', 1)
    monkeypatch.setattr(ticketmaster, 'request', lambda *a, **kw: Response({'page': {'totalPages': 5}}))
    assert ticketmaster.discover(40.7, -74, 2, 14)['truncated']

def test_local_neighborhood_does_not_match_another_city():
    assert developments.out_of_market({'title':'Koreatown market opens','domain':'laist.com'})
    assert developments.out_of_market({'title':'New market in Koreatown, Los Angeles'})
    assert not developments.out_of_market({'title':'New market in Koreatown, Manhattan'})
