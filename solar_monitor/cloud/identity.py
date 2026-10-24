"""Cloud key registration (identity v2) and pinning of the cloud's signing keys.

Methods of CloudService, kept in their own module so service.py stays readable."""

from __future__ import annotations

import logging

import httpx


log = logging.getLogger(__name__)


class IdentityMixin:
    """Methods of CloudService; see service.py."""


    async def _identity_v2_upgrade_bg(self) -> None:
        """Identity v2 (#303), generate ed25519 keypair if missing,
        register public key with cloud if cloud doesn't have it yet.

        Idempotent and best-effort. Failures here log + move on,
        v1 bearer-token auth still works during the transition, so
        a cloud or network blip doesn't block heartbeats.

        Phase 1 just registers the keypair. Phases 2+ will start
        actually signing things with it.
        """
        try:
            from ..auth import load_or_create as _kp_load
            from ..auth.keypair import fingerprint as _kp_fp_disk
        except ImportError as e:
            log.debug("identity v2 module not available: %s, skipping", e)
            return

        try:
            keypair = _kp_load()
        except Exception:
            log.exception("identity v2: keypair load/generate failed")
            return

        endpoint = self.cfg.endpoint.rstrip("/")
        status_url = f"{endpoint}/api/internal/identity/v2/status"
        upgrade_url = f"{endpoint}/api/internal/identity/v2/upgrade"
        headers = {
            "Authorization": f"Bearer {self.cfg.bearer_token}",
            "Content-Type":  "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                # Check current state first. If the cloud already knows
                # this fingerprint, there's nothing to do.
                r = await client.get(status_url, headers=headers)
                if r.status_code == 200:
                    body = r.json()
                    if body.get("fingerprint") == keypair.fingerprint:
                        log.info("identity v2: already registered (fingerprint=%s)",
                                 keypair.fingerprint)
                        # Phase 3 (#305): /status now also returns OIDC
                        # client config, capture it so existing v2
                        # appliances pick up LAN OIDC on boot without
                        # having to re-trigger /upgrade.
                        if all(body.get(k) for k in (
                            "oidc_client_id", "oidc_redirect_uri",
                            "jwks_url", "oidc_discovery_url",
                        )):
                            try:
                                from ..auth import oidc_config as _oidc_cfg
                                _oidc_cfg.save_from_cloud(
                                    endpoint=self.cfg.endpoint,
                                    client_id=body["oidc_client_id"],
                                    redirect_uri=body["oidc_redirect_uri"],
                                    jwks_url=body["jwks_url"],
                                    discovery_url=body["oidc_discovery_url"],
                                )
                                await self._pin_signing_keys()
                            except Exception:
                                log.exception(
                                    "identity v2: failed to persist OIDC "
                                    "config from /status response",
                                )
                        # Phase 6B (#308), ensure mTLS leaf cert is
                        # current. Idempotent; returns immediately if
                        # cert is fresh. Until Phase 6B-B wires the
                        # heartbeat to use it, the cert just sits
                        # ready on disk.
                        try:
                            from . import mtls_client as _mtls
                            await _mtls.ensure_cert(
                                endpoint=endpoint,
                                bearer_token=self.cfg.bearer_token,
                            )
                        except Exception:
                            log.exception(
                                "identity v2 phase 6B: mTLS cert "
                                "issuance failed (non-fatal, heartbeat "
                                "still uses bearer)"
                            )
                        return
                elif r.status_code == 404:
                    # Cloud doesn't have the endpoint yet (older cloud
                    # deploy than appliance). Skip silently; we'll try
                    # again on next boot.
                    log.debug("identity v2: cloud endpoint 404, older deploy?")
                    return

                # Upload our public key. Cloud will either register
                # (first time) or rotate (different fingerprint than
                # what's stored).
                payload = {
                    "public_key_b64": keypair.public_key_b64(),
                    "fingerprint":    keypair.fingerprint,
                }
                r = await client.post(upgrade_url, json=payload, headers=headers)
                if r.status_code >= 400:
                    log.warning("identity v2 upgrade HTTP %s: %s",
                                r.status_code, r.text[:200])
                    return
                body = r.json()
                log.info("identity v2 upgrade %s: appliance keypair "
                         "registered with cloud (fingerprint=%s)",
                         body.get("result"), keypair.fingerprint)

                # Phase 3 (#305): persist OIDC client config the cloud
                # handed back. We capture all four fields atomically so
                # downstream code can assume "if oidc_config.load() is
                # not None, all fields are present and consistent".
                # Older clouds (pre-Phase 2) won't return these fields
                #, skip silently in that case so we don't churn the
                # disk on partial upgrade.
                if all(body.get(k) for k in (
                    "oidc_client_id", "oidc_redirect_uri",
                    "jwks_url", "oidc_discovery_url",
                )):
                    try:
                        from ..auth import oidc_config as _oidc_cfg
                        _oidc_cfg.save_from_cloud(
                            endpoint=self.cfg.endpoint,
                            client_id=body["oidc_client_id"],
                            redirect_uri=body["oidc_redirect_uri"],
                            jwks_url=body["jwks_url"],
                            discovery_url=body["oidc_discovery_url"],
                        )
                        await self._pin_signing_keys()
                    except Exception:
                        # Non-fatal, Phase 3 OIDC client is additive;
                        # legacy cookie auth still works without it.
                        log.exception(
                            "identity v2 phase 3: failed to persist OIDC "
                            "config; LAN OIDC login will fall back to "
                            "the legacy password flow",
                        )

                # Phase 6B (#308), issue mTLS leaf cert. Runs on
                # the SAME upgrade-bg task path so a newly-upgraded
                # appliance has a cert by the end of its first boot.
                try:
                    from . import mtls_client as _mtls
                    await _mtls.ensure_cert(
                        endpoint=endpoint,
                        bearer_token=self.cfg.bearer_token,
                    )
                except Exception:
                    log.exception(
                        "identity v2 phase 6B: mTLS cert issuance "
                        "failed (non-fatal, heartbeat still uses bearer)"
                    )
        except Exception as e:
            log.warning("identity v2: cloud round-trip failed: %s, "
                        "will retry on next boot", e)


    async def _pin_signing_keys(self) -> None:
        """Remember the cloud's command-signing keys as seen at pairing."""
        from .command_verify import pin_current_keys
        try:
            await pin_current_keys()
        except Exception:
            log.warning("could not pin the cloud's signing keys yet; "
                        "will pin the first key that verifies a command", exc_info=True)
