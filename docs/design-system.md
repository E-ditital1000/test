# A1 360 — the design system in code

The approved specification (v2) is implemented. This is the map from that
document to the files, so a Wave 2 agent extends the system instead of
inventing a parallel one.

Bootstrap has been removed. There is no CSS framework.

## Where everything lives

| Thing | File |
| --- | --- |
| Tokens, components, breakpoints | [`static/css/a1.css`](../static/css/a1.css) |
| The eleven icons, as an inline SVG sprite | [`templates/shell/_icons.html`](../templates/shell/_icons.html) |
| Document skeleton (head, fonts, sprite) | [`templates/shell/_document.html`](../templates/shell/_document.html) |
| Office shell — sidebar, title bar, content | [`templates/base.html`](../templates/base.html) |
| Phone shell — navy header, tab bar | [`templates/shell/field.html`](../templates/shell/field.html) |
| Empty / filtered / refused states | `templates/shell/_empty.html`, `_empty_filtered.html`, `_deny.html` |
| Content rules as filters | [`accounts/templatetags/a1.py`](../accounts/templatetags/a1.py) |
| Navigation, both surfaces | [`accounts/navigation.py`](../accounts/navigation.py) |

## Writing a screen

**Office:**

```django
{% extends "base.html" %}
{% load a1 %}
{% block page_title %}Ticket #1041{% endblock %}
{% block page_sub %}Duport Road Clinic · raised {{ t.created_at|a1datetime }}{% endblock %}
{% block page_actions %}<a class="btn btn-pri" href="…">Convert to project</a>{% endblock %}
{% block content %} … {% endblock %}
```

**Phone:**

```django
{% extends "shell/field.html" %}
{% block phone_title %}Today · 3 Sept{% endblock %}
{% block phone_sub %}M. Toe · Technician{% endblock %}
{% block content %} … {% endblock %}
{# Inside a running assessment only — a form holding unsaved local
   answers must not offer a one-tap exit: #}
{% block tabs %}{% endblock %}
```

## The rules that are enforced, not just documented

1. **Gold is the committing action, and nothing else.** `.btn-pri` only.
   It appears on no status pill — in-progress is teal, pending-sync indigo.
   One gold control per screen.
2. **Focus is two tokens.** Navy `#2A1670` on light, gold on navy, set by
   the shell. Gold on white is 1.52:1 and is never used. Never remove it.
3. **Disabled versus absent.** Disabled (`.btn:disabled`) is for a control
   a changed condition would enable — Return before a comment is typed. A
   control the user's role can never use is **absent**. This is the
   permission model restated at component level.
4. **Empty and filtered are two components.** `_empty.html` names the first
   action; `_empty_filtered.html` names what emptied the view, says how many
   records exist in total, and offers to clear.
5. **Four mobile tabs, maximum.** A Django system check (`accounts.E003`)
   fails the build on a fifth.
6. **Navigation is generated from permissions** on both surfaces. Add a
   module to `NAV_ITEMS` with its targets; never hardcode a role name.
   `accounts.E002` fails the build on an unknown permission code.
7. **Pending sync is a device state.** It belongs on the phone and in the
   technician's own view. It must never appear in a supervisor's list as
   though it were a server status.

## Content rules — use the filters

Load with `{% load a1 %}`. These exist so eight modules format identically.

| Filter | Output | Use for |
| --- | --- | --- |
| `\|a1date` | `3 Sept` · `2 Jul 2025` across years | dates |
| `\|a1datetime` | `3 Sept 11:42` | event timestamps |
| `\|a1name` | `M. Toe` | operational context — tickets, jobs, logs |
| `\|a1register_name` | `Toe, Moses` | HR registers and reports only |
| `\|a1money` | `$18,400` · `$178.50` | currency |
| `\|a1hours` | `6h 12m` | elapsed time on field surfaces |
| `\|a1metres` | `± 9 m` | GPS accuracy and distances |
| `{% a1greeting %}` | `Good morning` | landing screens |

Never format a date numerically. `3/9` and `9/3` are both read in Liberia.
Add `.mono` to any column of figures, clock times or IDs.

## Component quick reference

```html
<button class="btn btn-pri">Approve</button>        <!-- gold, one per screen -->
<button class="btn btn-sec">Reassign</button>
<button class="btn btn-gho">Open</button>
<button class="btn btn-dgr">Return</button>
<button class="btn btn-pri is-loading">Saving</button>
<button class="btn btn-pri" disabled>Approve</button>

<span class="pill p-open"><i></i>Open</span>        <!-- blue -->
<span class="pill p-prog"><i></i>In progress</span> <!-- teal -->
<span class="pill p-wait"><i></i>Awaiting approval</span>
<span class="pill p-appr"><i></i>Approved</span>
<span class="pill p-over"><i></i>Overdue</span>
<span class="pill p-sync"><i></i>Pending sync</span> <!-- device state only -->
<span class="pill p-clos"><i></i>Closed</span>

<div class="metric alert"><span>Label</span><b>82%</b><em>context</em></div>
<div class="card compact"> … </div>                 <!-- bulk-read tables -->
<div class="a1-skel t"></div>                       <!-- loading rows -->
```

Wrap any wide table in `<div class="scroll-x">`. The page body must never
scroll sideways.

## Breakpoints

`1440` design width · `1366` keeps the sidebar, tightens padding · `1024`
collapses the sidebar to a 64 px icon rail · below `900` the office modules
are unsupported in Phase One and the mobile surfaces take over.

## Two things I changed to keep the copy honest

- **Refused requests are now audit-logged.** The refusal screen tells the
  user the request is on the record, so `accounts.views.permission_denied`
  writes an `access.refused` row with actor, path and missing permission.
- **The lockout copy states the real rule.** The specification's locked
  panel says an Admin must release the account and the lock never expires.
  The Wave 1 build locks for `LOGIN_LOCKOUT_MINUTES` (15) and then releases
  itself. The screen currently says both — it unlocks on a timer *or* an
  Admin can release it now. **If the spec's rule is the intended one, that
  is a behaviour change and needs a decision**: an admin-release-only lock
  means a technician who fatfingers their password five times at 06:00 on a
  site with no signal cannot work until someone in the office is reachable.
  My recommendation is to keep the timer and amend the spec copy.
