"""Audit explicit HTTP commands separately from domain intent/outcome events."""
from uuid import UUID
from domaincheck.application.audit_log import persist

FEATURES = {'quick-reports': 'quick_report', 'domain-checks': 'check_domain',
    'domain-worker': 'domain_worker', 'cloudflare-worker': 'cloudflare_worker',
    'provider-replies': 'provider_replies', 'mail-accounts': 'mail_accounts',
    'settings': 'mail_profile', 'users': 'users', 'roles': 'roles',
    'cache': 'cache', 'form-tasks': 'quick_report', 'audit-logs': 'audit_logs'}


async def capture_command(request, response):
    actor = getattr(request.state, 'audit_actor', None)
    route = request.scope.get('route')
    path = getattr(route, 'path', '')
    prefix = request.app.state.settings.api_prefix + '/'
    feature = FEATURES.get(path.removeprefix(prefix).split('/')[0])
    if actor is None or feature is None: return
    denied = response.status_code == 403
    if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'} and not denied: return
    if '/reviews/cloaking' in path: feature = 'cloaking_review'
    elif '/reviews/evidence' in path: feature = 'domain_evidence_review'
    identity = None
    for value in request.path_params.values():
        try: identity = UUID(str(value)); break
        except ValueError: pass
    # Route function names are code-owned. No path/query, body, headers, exception
    # text is copied into audit metadata.
    operation = getattr(getattr(route, 'endpoint', None), '__name__', 'command')
    outcome = 'denied' if denied else 'accepted' if response.status_code < 400 else 'failed'
    await persist(request, owner=actor.user_id, actor=actor.user_id,
        action='security.access_denied' if denied else feature + '.request_' + operation,
        resource_type=feature, resource_id=identity, outcome=outcome,
        metadata={'http_status': response.status_code})
