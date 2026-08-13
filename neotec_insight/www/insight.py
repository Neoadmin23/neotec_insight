# v2.39.3 — the SPA shell must never be cached by Frappe's website cache:
# a stale insight.html keeps pointing at an old hashed bundle after deploys,
# which is exactly how a fixed build can appear "still broken".
no_cache = 1


def get_context(context):
    context.no_cache = 1
    return context
