"""Shared Chromium launch flags / profile prefs for Playwright."""

from __future__ import annotations

import json
from pathlib import Path

# Suppress translate bubble + password save / autofill prompts.
CHROMIUM_DISABLE_PROMPT_ARGS: list[str] = [
    "--disable-infobars",
    "--disable-translate",
    "--no-first-run",
    "--no-default-browser-check",
    "--password-store=basic",
    "--disable-features="
    "Translate,TranslateUI,OptimizationHints,"
    "PasswordManagerOnboarding,PasswordLeakDetection,"
    "AutofillServerCommunication,AutofillEnableAccountWalletStorage",
]


def prepare_chromium_profile(user_data_dir: Path | str) -> Path:
    """Ensure profile prefs disable translate + password manager prompts."""
    root = Path(user_data_dir)
    default = root / "Default"
    default.mkdir(parents=True, exist_ok=True)
    prefs_path = default / "Preferences"
    data: dict = {}
    if prefs_path.is_file():
        try:
            data = json.loads(prefs_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:  # noqa: BLE001
            data = {}

    translate = data.setdefault("translate", {})
    if isinstance(translate, dict):
        translate["enabled"] = False

    data["credentials_enable_service"] = False
    data["credentials_enable_autosignin"] = False

    profile = data.setdefault("profile", {})
    if isinstance(profile, dict):
        profile["password_manager_enabled"] = False
        profile["password_manager_leak_detection"] = False

    autofill = data.setdefault("autofill", {})
    if isinstance(autofill, dict):
        autofill["profile_enabled"] = False
        autofill["credit_card_enabled"] = False

    prefs_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
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
