"""Shared Chromium launch flags / profile prefs for Playwright."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Chromium still shows translate / save-password bubbles unless both flags
# *and* profile prefs are set (and the browser is not already running).
CHROMIUM_DISABLE_PROMPT_ARGS: list[str] = [
    "--disable-infobars",
    "--disable-translate",
    "--disable-features="
    "Translate,TranslateUI,TranslateKit,LanguageDetectionAPI,"
    "OptimizationHints,PasswordManager,PasswordManagerOnboarding,"
    "PasswordLeakDetection,PasswordCheck,PasswordImport,"
    "PasswordGeneration,ImprovedPasswordChange,"
    "AutofillServerCommunication,AutofillEnableAccountWalletStorage",
    "--disable-password-manager-reauthentication",
    "--disable-password-generation",
    "--password-store=basic",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--disable-component-update",
    "--lang=zh-CN",
    "--accept-lang=zh-CN,zh,en-US,en",
]


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _deep_merge(base: dict, updates: dict) -> dict:
    out = dict(base)
    for key, value in updates.items():
        existing = out.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            out[key] = _deep_merge(existing, value)
        else:
            out[key] = value
    return out


def _prompt_block_prefs() -> dict[str, Any]:
    return {
        "credentials_enable_service": False,
        "credentials_enable_autosignin": False,
        "translate": {"enabled": False},
        "translate_blocked_languages": ["zh-CN", "zh", "en"],
        "intl": {"accept_languages": "zh-CN,zh,en-US,en"},
        "profile": {
            "password_manager_enabled": False,
            "password_manager_leak_detection": False,
            "password_manager_auto_signin": False,
            "default_content_setting_values": {"automatic_downloads": 1},
        },
        "password_manager": {
            "saving_enabled": False,
            "leak_detection_enabled": False,
            "account_storage_enabled": False,
        },
        "autofill": {
            "profile_enabled": False,
            "credit_card_enabled": False,
            "enabled": False,
        },
        "signin": {"allowed": False, "allowed_on_next_startup": False},
        "safebrowsing": {
            "enabled": False,
            "enhanced": False,
        },
    }


def prepare_chromium_profile(user_data_dir: Path | str) -> Path:
    """Write profile prefs that disable translate + password-save prompts.

    Must run while Chromium is closed; Chrome overwrites Preferences on exit.
    """
    root = Path(user_data_dir)
    default = root / "Default"
    default.mkdir(parents=True, exist_ok=True)
    block = _prompt_block_prefs()
    for name in ("Preferences", "Secure Preferences"):
        path = default / name
        _write_json(path, _deep_merge(_load_json(path), block))
    local_state_path = root / "Local State"
    local_state = _load_json(local_state_path)
    local_state = _deep_merge(
        local_state,
        {
            "profile": {
                "password_manager_enabled": False,
            },
            "intl": {"accept_languages": "zh-CN,zh,en-US,en"},
        },
    )
    _write_json(local_state_path, local_state)
    return root


def chromium_launch_args(
    *,
    bounds: tuple[int, int, int, int] | None = None,
    extra: list[str] | None = None,
) -> list[str]:
    """Build Chromium ``args`` for persistent context launch."""
    args = list(CHROMIUM_DISABLE_PROMPT_ARGS)
    if extra:
        args.extend(extra)
    if bounds is not None:
        left, top, width, height = bounds
        args.extend(
            [
                f"--window-position={int(left)},{int(top)}",
                f"--window-size={max(400, int(width))},{max(400, int(height))}",
            ]
        )
    return args


def chromium_persistent_kwargs(
    user_data_dir: Path | str,
    *,
    bounds: tuple[int, int, int, int] | None = None,
    extra_args: list[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Prefs + args + locale for ``launch_persistent_context``."""
    profile = prepare_chromium_profile(user_data_dir)
    kwargs: dict[str, Any] = {
        "user_data_dir": str(profile),
        "headless": False,
        "no_viewport": True,
        "locale": "zh-CN",
        "args": chromium_launch_args(bounds=bounds, extra=extra_args),
        "ignore_default_args": ["--enable-automation"],
    }
    kwargs.update(overrides)
    return kwargs
