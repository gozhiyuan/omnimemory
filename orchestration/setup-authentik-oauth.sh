#!/bin/bash
# Setup Authentik OAuth Application for OmniMemory
# Run this after Authentik starts for the first time
# This script is idempotent - safe to run multiple times

set -e

# Default values (can be overridden by env vars)
ADMIN_EMAIL="${AUTHENTIK_BOOTSTRAP_EMAIL:-admin@localhost}"
ADMIN_PASSWORD="${AUTHENTIK_BOOTSTRAP_PASSWORD:-admin123}"
WEB_PORT="${WEB_PORT:-3000}"

echo "Setting up Authentik for OmniMemory..."

docker exec lifelog-authentik ak shell -c "
import os
from authentik.flows.models import Flow
from authentik.providers.oauth2.models import OAuth2Provider, RedirectURI, RedirectURIMatchingMode
from authentik.core.models import Application, User
from authentik.crypto.models import CertificateKeyPair
from time import sleep

# ============================================================
# 1. Configure admin user
# ============================================================
admin_email = '$ADMIN_EMAIL'
admin_password = '$ADMIN_PASSWORD'

user = User.objects.filter(username='akadmin').first()
if user:
    user.email = admin_email
    user.set_password(admin_password)
    user.save()
    print(f'Admin user configured: akadmin (email: {admin_email})')
else:
    print('Warning: akadmin user not found')

# ============================================================
# 2. Create OAuth2 Provider
# ============================================================
flow = None
flow_slugs = [
    'default-provider-authorization-implicit-consent',
    'default-provider-authorization-explicit-consent',
]
for _ in range(30):
    for slug in flow_slugs:
        flow = Flow.objects.filter(slug=slug).first()
        if flow:
            break
    if flow:
        break
    sleep(2)

if flow is None:
    available = list(Flow.objects.values_list('slug', flat=True))
    raise Exception(f'Authorization flow not found. Available flows: {available}')

provider, created = OAuth2Provider.objects.get_or_create(
    name='omnimemory-provider',
    defaults={
        'authorization_flow': flow,
        'client_type': 'public',
        'client_id': 'omnimemory',
        'access_code_validity': 'minutes=10',
        'access_token_validity': 'hours=1',
        'refresh_token_validity': 'days=30',
        'include_claims_in_id_token': True,
        'sub_mode': 'user_id',
        'issuer_mode': 'per_provider',
    }
)

# Set redirect URIs (always update to ensure correct port)
web_port = '$WEB_PORT'
provider.redirect_uris = [
    RedirectURI(url=f'http://localhost:{web_port}/', matching_mode=RedirectURIMatchingMode.STRICT),
    RedirectURI(url=f'http://localhost:{web_port}', matching_mode=RedirectURIMatchingMode.STRICT),
]

if provider.signing_key_id is None:
    signing_key = CertificateKeyPair.objects.filter(name='authentik Internal JWT Certificate').first()
    if signing_key is None:
        signing_key = CertificateKeyPair.objects.first()
    if signing_key:
        provider.signing_key = signing_key
        print(f'Assigned signing key: {signing_key.name}')
    else:
        print('Warning: no signing key found for OAuth provider')

provider.save()

# Add default scope mappings (email, profile, openid) so JWT includes user claims
from authentik.providers.oauth2.models import ScopeMapping
default_scopes = ScopeMapping.objects.filter(managed__startswith='goauthentik.io/providers/oauth2/scope-')
if default_scopes.exists():
    provider.property_mappings.set(default_scopes)
    provider.save()
    print(f'Configured {default_scopes.count()} scope mappings')

if created:
    print(f'OAuth2 Provider created: omnimemory (port {web_port})')
else:
    print(f'OAuth2 Provider updated: omnimemory (port {web_port})')

# ============================================================
# 3. Create Application
# ============================================================
app, app_created = Application.objects.update_or_create(
    slug='omnimemory',
    defaults={
        'name': 'OmniMemory',
        'provider': provider,
        'meta_launch_url': f'http://localhost:{web_port}/',
        'meta_description': 'Personal Memory AI System',
    }
)

if app_created:
    print('Application created: omnimemory')
else:
    print('Application updated: omnimemory')

print('Setup complete!')
"

echo ""
echo "Authentik setup complete!"
echo "  Login: http://localhost:3000"
echo "  Username: akadmin"
echo "  Password: $ADMIN_PASSWORD"
