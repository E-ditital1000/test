"""
One pager for every list in the system.

Two reasons this is shared rather than done per screen. The obvious one is
that eight modules should not invent eight pagers. The other is that several
of these lists were capped with a slice — `entries[:300]` — which looks like
paging and is not: the rows past the cap are simply unreachable, and nothing
on the screen says so. A pager is the honest version of that cap.

The page size is deliberately not a setting. It is a design decision about
how much a person can scan, and the answer does not vary by deployment.
"""
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator

# Enough to fill a laptop screen without scrolling far, few enough that the
# query stays cheap on a phone over a weak connection.
PER_PAGE = 25


def paginate(request, rows, per_page=PER_PAGE, param="page"):
    """
    Return one page of `rows`, plus what a pager needs to render itself.

    Out-of-range and non-numeric page numbers resolve to a real page rather
    than raising: a stale bookmark or a hand-typed URL should show the list,
    not a 404.
    """
    paginator = Paginator(rows, per_page)
    requested = request.GET.get(param) or 1
    try:
        page = paginator.page(requested)
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        # Django raises the same error at both ends. Which end matters: past
        # the last page the reader wants the last rows, but `page=0` is a
        # typo or a bad link, and landing them on the final page instead of
        # the first would be baffling.
        try:
            too_low = int(requested) < 1
        except (TypeError, ValueError):
            too_low = True
        page = paginator.page(1 if too_low else paginator.num_pages)

    # Every filter and search term the user already applied has to survive
    # the click to page 2, so the pager links carry the rest of the query.
    params = request.GET.copy()
    params.pop(param, None)
    page.base_query = params.urlencode()
    page.param = param
    return page
