#!/usr/bin/env python3
"""
VSCodium GitHub Copilot Auto-Installer v2.1

Automatically finds and installs compatible Copilot extensions for your VSCodium version
by checking which API proposals are actually available in your VSCodium installation.

Features:
- Smart API compatibility detection
- Automatic version finding
- User-level product.json configuration (survives updates)
- Model selector enablement
- Full settings configuration

Usage:
    python3 codium-copilot.py

Requirements:
    - Python 3.7+
    - requests
    - packaging

Author: GitHub Community
License: MIT
"""

import json
import os
import re
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    import requests
    from packaging import version
except ImportError as e:
    print(f"Error: Missing required dependency: {e.name}")
    print("\nPlease install required packages:")
    print("  pip install requests packaging")
    sys.exit(1)


# ============================================================================
# Constants
# ============================================================================

API_URL = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"
API_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 120
DOWNLOAD_CHUNK_SIZE = 8192
MAX_VERSIONS_TO_CHECK = 200
INSTALL_RETRY_COUNT = 2
INSTALL_RETRY_DELAY = 3

# API Flags
API_FLAGS = 0x1 | 0x2 | 0x10

# User config directories
USER_CONFIG_DIRS = [
    Path.home() / '.config' / 'VSCodium',
    Path.home() / '.config' / 'Code - OSS',
    Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'VSCodium',
]

# System product.json locations
SYSTEM_PRODUCT_JSON_PATHS = [
    Path('/usr/share/codium/resources/app/product.json'),
    Path('/opt/vscodium-bin/resources/app/product.json'),
    Path('/opt/VSCodium/resources/app/product.json'),
    Path('/Applications/VSCodium.app/Contents/Resources/app/product.json'),
    Path('/snap/codium/current/usr/share/codium/resources/app/product.json'),
]

# Runtime extensionApiProposals.js locations (authoritative list of implemented proposals)
RUNTIME_API_PROPOSALS_PATHS = [
    Path('/usr/share/codium/resources/app/out/vs/workbench/api/common/extensionApiProposals.js'),
    Path('/opt/vscodium-bin/resources/app/out/vs/workbench/api/common/extensionApiProposals.js'),
    Path('/opt/VSCodium/resources/app/out/vs/workbench/api/common/extensionApiProposals.js'),
    Path('/snap/codium/current/usr/share/codium/resources/app/out/vs/workbench/api/common/extensionApiProposals.js'),
]

# macOS config directory
if sys.platform == 'darwin':
    USER_CONFIG_DIRS.insert(0, Path.home() / 'Library' / 'Application Support' / 'VSCodium')
    RUNTIME_API_PROPOSALS_PATHS.insert(0, Path(
        '/Applications/VSCodium.app/Contents/Resources/app/out'
        '/vs/workbench/api/common/extensionApiProposals.js'
    ))

# Windows config directory
if sys.platform == 'win32':
    appdata = os.environ.get('APPDATA')
    if appdata:
        USER_CONFIG_DIRS.insert(0, Path(appdata) / 'VSCodium')
    localappdata = os.environ.get('LOCALAPPDATA')
    if localappdata:
        RUNTIME_API_PROPOSALS_PATHS.insert(0, Path(localappdata) / 'Programs' / 'VSCodium'
                                           / 'resources' / 'app' / 'out' / 'vs' / 'workbench'
                                           / 'api' / 'common' / 'extensionApiProposals.js')

# Required settings for full Copilot functionality
COPILOT_SETTINGS = {
    "github.copilot.editor.enableAutoCompletions": True,
    "github.copilot.chat.experimental.modelSelection": True,
    "github.copilot.chat.modelPicker.enabled": True,
    "github.copilot.advanced": {
        "debug.enableModelSelection": True
    }
}


@dataclass
class Extension:
    """Extension metadata."""
    extension_id: str
    name: str
    install_order: int


# Only GitHub Copilot Chat is needed - includes inline completions
EXTENSIONS = [
    Extension("GitHub.copilot-chat", "GitHub Copilot Chat", 1),
]


# ============================================================================
# Terminal Colors
# ============================================================================

class Colors:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

    @classmethod
    def disable(cls) -> None:
        """Disable all colors (for non-TTY terminals)."""
        cls.HEADER = ''
        cls.OKBLUE = ''
        cls.OKCYAN = ''
        cls.OKGREEN = ''
        cls.WARNING = ''
        cls.FAIL = ''
        cls.ENDC = ''
        cls.BOLD = ''
        cls.UNDERLINE = ''


if not sys.stdout.isatty():
    Colors.disable()


# ============================================================================
# Output Functions
# ============================================================================

def print_banner() -> None:
    """Print application banner."""
    banner = f"""
{Colors.OKCYAN}{Colors.BOLD}╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║        VSCodium GitHub Copilot Auto-Installer v2.1          ║
║          With Smart API Compatibility Detection             ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝{Colors.ENDC}
"""
    print(banner)


def print_step(step_num: int, total_steps: int, message: str) -> None:
    """Print a numbered step header."""
    print(f"\n{Colors.BOLD}{Colors.OKBLUE}[{step_num}/{total_steps}]{Colors.ENDC} "
          f"{Colors.BOLD}{message}{Colors.ENDC}")


def print_success(message: str, indent: int = 2) -> None:
    """Print success message."""
    print(f"{' ' * indent}{Colors.OKGREEN}✓{Colors.ENDC} {message}")


def print_error(message: str, indent: int = 2) -> None:
    """Print error message."""
    print(f"{' ' * indent}{Colors.FAIL}✗{Colors.ENDC} {message}")


def print_warning(message: str, indent: int = 2) -> None:
    """Print warning message."""
    print(f"{' ' * indent}{Colors.WARNING}⚠{Colors.ENDC} {message}")


def print_info(message: str, indent: int = 2) -> None:
    """Print info message."""
    print(f"{' ' * indent}{Colors.OKCYAN}ℹ{Colors.ENDC} {message}")


# ============================================================================
# Safety Checks
# ============================================================================

def check_not_running_in_codium() -> None:
    """Verify script is not running inside VSCodium terminal."""
    if os.environ.get('TERM_PROGRAM') == 'vscode':
        print_error("This script is running inside VSCodium!", 0)
        print_warning("VSCodium will be terminated during installation.", 0)
        print_info("Please run this script from a regular terminal.", 0)
        print(f"\n{Colors.WARNING}Exiting...{Colors.ENDC}\n")
        sys.exit(1)

    vscode_vars = [key for key in os.environ if key.startswith('VSCODE_')]
    if vscode_vars:
        print_warning("Detected VSCode-related environment variables:", 0)
        for var in vscode_vars[:3]:
            value = os.environ[var]
            display_value = value[:50] + '...' if len(value) > 50 else value
            print_info(f"{var} = {display_value}", 4)

        try:
            response = input(f"\n{Colors.WARNING}Are you running this inside "
                             f"VSCodium? (y/N): {Colors.ENDC}").strip().lower()
            if response in ('y', 'yes'):
                print_error("Please run this script from a regular terminal.", 0)
                sys.exit(1)
        except (EOFError, KeyboardInterrupt):
            print("\n")
            sys.exit(130)


def check_dependencies() -> None:
    """Check that required system commands are available."""
    required_commands = ['codium', 'pgrep', 'pkill']
    missing = []

    for cmd in required_commands:
        try:
            subprocess.run([cmd, '--version'], capture_output=True, check=False, timeout=5)
        except FileNotFoundError:
            missing.append(cmd)
        except subprocess.TimeoutExpired:
            pass

    if missing:
        print_error("Missing required commands:", 0)
        for cmd in missing:
            print_info(cmd, 4)
        sys.exit(1)


# ============================================================================
# API Proposal Detection
# ============================================================================

def get_runtime_api_proposals(proposals_js_path: Path) -> Optional[Set[str]]:
    """Extract implemented API proposal names from VSCodium's runtime JS file.

    The extensionApiProposals.js file is the authoritative source for which
    proposals VSCodium actually implements at runtime.  Each entry has the form:
        "proposalName": { version: N, proposal: "..." }
    """
    try:
        content = proposals_js_path.read_text(encoding='utf-8')
        # Match both quoted and unquoted keys followed by '{ version:'
        # This is the exact structure used in allApiProposals.
        names = re.findall(
            r'["\']?([a-zA-Z][a-zA-Z0-9_]*)["\']?\s*:\s*\{\s*version\s*:\s*\d',
            content
        )
        proposals = set(names)
        # Exclude JS reserved/noise words that can match the pattern
        proposals.discard('version')
        if proposals:
            return proposals
    except Exception:
        pass
    return None


def get_supported_api_proposals() -> Set[str]:
    """Get the list of API proposals that VSCodium actually supports.

    Primary source: extensionApiProposals.js (runtime-implemented proposals).
    Fallback: extensionEnabledApiProposals in system product.json (permission
    list only — less accurate, may contain stale entries).
    """
    print_info("Detecting supported API proposals...")

    # --- Primary: read from VSCodium's compiled runtime proposals file ---
    for runtime_path in RUNTIME_API_PROPOSALS_PATHS:
        if runtime_path.exists():
            print_success(f"Found runtime proposals file: {runtime_path}", 4)
            proposals = get_runtime_api_proposals(runtime_path)
            if proposals:
                print_success(
                    f"Found {len(proposals)} runtime-implemented API proposals", 4
                )
                sample = sorted(proposals)[:5]
                for prop in sample:
                    print_info(prop, 6)
                if len(proposals) > 5:
                    print_info(f"... and {len(proposals) - 5} more", 6)
                return proposals
            print_warning("Could not parse runtime proposals file, falling back", 4)

    # --- Fallback: read granted proposals from system product.json ---
    # Note: extensionEnabledApiProposals is a per-extension permission list.
    # It reflects what extensions are *allowed* to use, not what VSCodium
    # implements.  Use only when the runtime file is unavailable.
    system_product_json = None
    for path in SYSTEM_PRODUCT_JSON_PATHS:
        if path.exists():
            system_product_json = path
            break

    if not system_product_json:
        print_warning("Could not find system product.json", 4)
        print_info("Will check all extension versions", 4)
        return set()

    print_success(f"Found system product.json: {system_product_json}", 4)
    print_warning(
        "Runtime proposals file not found; using product.json fallback (less accurate)", 4
    )

    try:
        with system_product_json.open('r') as f:
            product_data = json.load(f)

        all_proposals = set()
        enabled_proposals = product_data.get('extensionEnabledApiProposals', {})

        for ext_id, proposals in enabled_proposals.items():
            for proposal in proposals:
                base_name = proposal.split('@')[0]
                all_proposals.add(base_name)

        print_success(f"Found {len(all_proposals)} supported API proposals", 4)

        sample = sorted(list(all_proposals))[:5]
        for prop in sample:
            print_info(prop, 6)
        if len(all_proposals) > 5:
            print_info(f"... and {len(all_proposals) - 5} more", 6)

        return all_proposals

    except json.JSONDecodeError as e:
        print_error(f"Invalid JSON in system product.json: {e}", 4)
        return set()
    except Exception as e:
        print_error(f"Failed to read system product.json: {e}", 4)
        return set()


def normalize_api_proposal(proposal: str) -> str:
    """Normalize an API proposal name by removing version suffix."""
    return proposal.split('@')[0]


def check_api_compatibility(
    required_proposals: List[str],
    supported_proposals: Set[str]
) -> Tuple[bool, List[str]]:
    """Check if required API proposals are supported."""
    if not supported_proposals:
        return True, []

    unsupported = []
    for proposal in required_proposals:
        base_name = normalize_api_proposal(proposal)
        if base_name not in supported_proposals:
            unsupported.append(proposal)

    is_compatible = len(unsupported) == 0
    return is_compatible, unsupported


# ============================================================================
# VSIX Extraction
# ============================================================================

def extract_api_proposals_from_vsix(vsix_path: Path, extension_id: str) -> List[str]:
    """Extract enabledApiProposals from a VSIX package.json."""
    try:
        with zipfile.ZipFile(vsix_path, 'r') as zip_file:
            try:
                package_json_data = zip_file.read('extension/package.json')
            except KeyError:
                package_json_data = zip_file.read('package.json')

            package_json = json.loads(package_json_data.decode('utf-8'))
            api_proposals = package_json.get('enabledApiProposals', [])

            return api_proposals

    except zipfile.BadZipFile:
        print_error(f"Invalid VSIX file: {vsix_path}", 4)
        return []
    except KeyError as e:
        print_error(f"Could not find package.json in VSIX: {e}", 4)
        return []
    except json.JSONDecodeError as e:
        print_error(f"Invalid JSON in package.json: {e}", 4)
        return []
    except Exception as e:
        print_error(f"Failed to extract API proposals: {e}", 4)
        return []


# ============================================================================
# VSCodium Configuration
# ============================================================================

def find_user_config_dir() -> Optional[Path]:
    """Find the VSCodium user configuration directory."""
    for config_dir in USER_CONFIG_DIRS:
        if config_dir.exists():
            return config_dir

    default_config = USER_CONFIG_DIRS[0]
    try:
        default_config.mkdir(parents=True, exist_ok=True)
        return default_config
    except OSError:
        return None


def update_user_product_json(
    extension_proposals: Dict[str, List[str]],
    supported_apis: Set[str]
) -> bool:
    """Create or update user-level product.json with API proposals."""
    print_info("Updating user-level product.json...")

    config_dir = find_user_config_dir()

    if not config_dir:
        print_error("Could not find or create VSCodium config directory")
        return False

    product_json_path = config_dir / 'product.json'

    try:
        existing_data = {}
        if product_json_path.exists():
            try:
                with product_json_path.open('r') as f:
                    existing_data = json.load(f)
            except json.JSONDecodeError:
                print_warning("Existing product.json is invalid, creating backup...", 4)
                backup_path = product_json_path.with_suffix('.json.backup')
                product_json_path.rename(backup_path)

        if 'extensionEnabledApiProposals' not in existing_data:
            existing_data['extensionEnabledApiProposals'] = {}

        # Update with extracted API proposals.
        # Normalize extension IDs to lowercase to match VSCodium's lookup convention.
        for ext_id, proposals in extension_proposals.items():
            if proposals:
                norm_id = ext_id.lower()
                existing_data['extensionEnabledApiProposals'][norm_id] = proposals
                print_success(f"Configured {len(proposals)} API proposals for {norm_id}", 4)

        # Add unversioned variants for all versioned proposals
        for ext_id in list(extension_proposals.keys()):
            norm_id = ext_id.lower()
            proposals = existing_data['extensionEnabledApiProposals'].get(norm_id, [])
            unversioned_added = []
            for proposal in proposals[:]:  # Copy to avoid modification during iteration
                if '@' in proposal:
                    base_name = proposal.split('@')[0]
                    if base_name not in proposals:
                        proposals.append(base_name)
                        unversioned_added.append(base_name)

            if unversioned_added:
                print_info(f"Added {len(unversioned_added)} unversioned API variants", 4)

        # Add critical proposals that might be missing — only if VSCodium actually
        # implements them (present in supported_apis).  Adding unimplemented proposals
        # causes VSCodium to report them as incompatible at runtime.
        critical_proposals = [
            'languageModelPicker',
            'chatParticipantPrivate',
            'defaultChatParticipant',
            'chatSessionsProvider',
            'chatProvider',
            'findFiles2',
        ]

        for ext_id in list(extension_proposals.keys()):
            norm_id = ext_id.lower()
            proposals = existing_data['extensionEnabledApiProposals'].get(norm_id, [])
            added_critical = []
            for prop in critical_proposals:
                # Only grant proposals that VSCodium can actually serve at runtime.
                # When supported_apis is empty, detection failed (no runtime file or
                # product.json found); fall back to allowing all critical proposals so
                # the extension has the best chance of working.
                if prop not in proposals and (not supported_apis or prop in supported_apis):
                    proposals.append(prop)
                    added_critical.append(prop)

            if added_critical:
                print_info(f"Added {len(added_critical)} critical proposals", 4)

        with product_json_path.open('w') as f:
            json.dump(existing_data, f, indent=2)

        print_success(f"Updated user product.json: {product_json_path}")
        return True

    except PermissionError:
        print_error(f"Permission denied when writing to {product_json_path}")
        return False
    except Exception as e:
        print_error(f"Unexpected error configuring product.json: {e}")
        return False


def update_user_settings() -> bool:
    """Update VSCodium settings to enable Copilot features."""
    print_info("Updating user settings...")

    config_dir = find_user_config_dir()
    if not config_dir:
        print_error("Could not find config directory")
        return False

    user_dir = config_dir / 'User'
    user_dir.mkdir(parents=True, exist_ok=True)

    settings_path = user_dir / 'settings.json'

    try:
        existing_settings = {}
        if settings_path.exists():
            try:
                with settings_path.open('r') as f:
                    existing_settings = json.load(f)
            except json.JSONDecodeError:
                print_warning("Existing settings.json is invalid, creating backup...", 4)
                backup_path = settings_path.with_suffix('.json.backup')
                settings_path.rename(backup_path)

        # Merge Copilot settings
        updated_count = 0
        for key, value in COPILOT_SETTINGS.items():
            if key not in existing_settings or existing_settings[key] != value:
                existing_settings[key] = value
                updated_count += 1

        with settings_path.open('w') as f:
            json.dump(existing_settings, f, indent=2)

        print_success(f"Updated settings.json: {settings_path}")
        if updated_count > 0:
            print_info(f"Added/updated {updated_count} Copilot settings", 4)
            print_info("✓ Model selector enabled", 4)
            print_info("✓ Auto-completions enabled", 4)
        else:
            print_info("All settings already configured", 4)

        return True

    except PermissionError:
        print_error(f"Permission denied when writing to {settings_path}")
        return False
    except Exception as e:
        print_error(f"Failed to update settings: {e}")
        return False


# ============================================================================
# VSCodium Version Detection
# ============================================================================

def get_codium_version() -> str:
    """Get installed VSCodium version."""
    try:
        result = subprocess.run(
            ['codium', '--version'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        version_str = result.stdout.strip().split('\n')[0]

        if not version_str or not version_str[0].isdigit():
            raise ValueError(f"Invalid version format: {version_str}")

        print_success(f"Detected VSCodium version: {Colors.BOLD}{version_str}{Colors.ENDC}")
        return version_str

    except subprocess.TimeoutExpired:
        print_error("VSCodium version check timed out")
        sys.exit(1)
    except FileNotFoundError:
        print_error("VSCodium not found in PATH")
        print_info("Please install VSCodium: https://vscodium.com/", 4)
        sys.exit(1)
    except (subprocess.CalledProcessError, ValueError) as e:
        print_error(f"Failed to get VSCodium version: {e}")
        sys.exit(1)


# ============================================================================
# Marketplace API
# ============================================================================

@dataclass
class CompatibleVersion:
    """Information about a compatible extension version."""
    version: str
    engine_requirement: str
    vsix_url: str


def query_marketplace(extension: Extension) -> Optional[Dict]:
    """Query Visual Studio Marketplace API for extension metadata."""
    print_info(f"Querying marketplace for {extension.name}...")

    payload = {
        "filters": [{
            "criteria": [{"filterType": 7, "value": extension.extension_id}],
            "pageSize": 1000
        }],
        "flags": API_FLAGS
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json;api-version=3.0-preview.1",
        "User-Agent": "VSCodium-Copilot-Installer/2.1"
    }

    try:
        response = requests.post(API_URL, json=payload, headers=headers, timeout=API_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        results = data.get('results', [])
        if not results or not results[0].get('extensions'):
            print_error(f"No data returned for {extension.name}")
            return None

        extension_data = results[0]['extensions'][0]
        total_versions = len(extension_data.get('versions', []))
        print_success(f"Found {total_versions} versions available")
        return extension_data

    except requests.exceptions.Timeout:
        print_error(f"Request timed out for {extension.name}")
    except requests.exceptions.RequestException as e:
        print_error(f"Network error: {e}")
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        print_error(f"Invalid API response: {e}")

    return None


def parse_engine_requirement(engine_str: str) -> str:
    """Parse engine requirement string."""
    return engine_str.lstrip('^>=~')


def is_version_compatible(vscode_version: str, engine_requirement: str) -> bool:
    """Check if VSCode version satisfies engine requirement."""
    try:
        vscode_ver = version.parse(vscode_version)
        required_ver = version.parse(parse_engine_requirement(engine_requirement))
        return vscode_ver >= required_ver
    except version.InvalidVersion as e:
        print_warning(f"Version comparison failed: {e}", 4)
        return False


def find_compatible_version_with_api_check(
    extension_data: Dict,
    target_version: str,
    extension_name: str,
    extension_id: str,
    supported_apis: Set[str]
) -> Optional[CompatibleVersion]:
    """Find highest compatible version with API compatibility checking."""
    print_info(f"Searching for compatible version of {extension_name}...")
    print_info("Will verify API proposal compatibility for each version", 4)

    versions = extension_data.get('versions', [])
    if not versions:
        print_error("No versions available")
        return None

    total_to_check = min(len(versions), MAX_VERSIONS_TO_CHECK)
    print_info(f"Checking up to {total_to_check} of {len(versions)} available versions...", 4)

    checked_count = 0
    skipped_prerelease = 0
    skipped_incompatible_engine = 0
    skipped_incompatible_api = 0

    for ver in versions[:MAX_VERSIONS_TO_CHECK]:
        checked_count += 1
        ver_str = ver.get('version', 'unknown')

        properties = {prop['key']: prop['value'] for prop in ver.get('properties', [])}

        if properties.get('Microsoft.VisualStudio.Code.PreRelease') == 'true':
            skipped_prerelease += 1
            continue

        engine = properties.get('Microsoft.VisualStudio.Code.Engine')
        if not engine:
            continue

        if checked_count <= 10:
            print_info(f"Checking v{ver_str} (requires {engine})...", 4)

        if not is_version_compatible(target_version, engine):
            skipped_incompatible_engine += 1
            continue

        files = ver.get('files', [])
        vsix_file = next(
            (f for f in files if f.get('assetType') == 'Microsoft.VisualStudio.Services.VSIXPackage'),
            None
        )

        if not vsix_file or not vsix_file.get('source'):
            continue

        print_info(f"Downloading v{ver_str} to check API compatibility...", 4)
        temp_vsix_path = Path(f"/tmp/{extension_id}-{ver_str}.vsix")

        try:
            response = requests.get(vsix_file['source'], stream=True, timeout=60)
            response.raise_for_status()

            with temp_vsix_path.open('wb') as f:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    f.write(chunk)

            required_apis = extract_api_proposals_from_vsix(temp_vsix_path, extension_id)

            if required_apis:
                is_api_compatible, unsupported = check_api_compatibility(required_apis, supported_apis)

                if not is_api_compatible:
                    print_warning(f"v{ver_str} requires unsupported APIs:", 4)
                    for api in unsupported[:3]:
                        print_info(api, 6)
                    if len(unsupported) > 3:
                        print_info(f"... and {len(unsupported) - 3} more", 6)

                    skipped_incompatible_api += 1
                    temp_vsix_path.unlink()
                    continue

            print_success(f"Found compatible version: {Colors.BOLD}{ver_str}{Colors.ENDC}")
            print_info(f"Engine requirement: {engine}", 4)
            if required_apis:
                print_info(f"All {len(required_apis)} API proposals are supported", 4)
            print_info(f"Checked {checked_count} versions "
                       f"(skipped {skipped_prerelease} pre-release, "
                       f"{skipped_incompatible_engine} wrong engine, "
                       f"{skipped_incompatible_api} incompatible APIs)", 4)

            temp_vsix_path.unlink()

            return CompatibleVersion(
                version=ver_str,
                engine_requirement=engine,
                vsix_url=vsix_file['source']
            )

        except Exception as e:
            print_warning(f"Failed to check v{ver_str}: {e}", 4)
            if temp_vsix_path.exists():
                temp_vsix_path.unlink()
            continue

    print_error(f"No compatible version found for {extension_name}")
    print_info(f"Checked {checked_count} versions:", 4)
    print_info(f"  - {skipped_prerelease} were pre-release", 4)
    print_info(f"  - {skipped_incompatible_engine} had incompatible engine requirements", 4)
    print_info(f"  - {skipped_incompatible_api} had unsupported API proposals", 4)
    return None


# ============================================================================
# Download & Installation
# ============================================================================

def download_vsix(url: str, filename: str, extension_name: str) -> Optional[Path]:
    """Download VSIX file with progress indication."""
    print_info(f"Downloading {extension_name}...")

    filepath = Path(filename)

    try:
        response = requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0

        with filepath.open('wb') as f:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                f.write(chunk)
                downloaded += len(chunk)

                if total_size > 0:
                    percent = (downloaded / total_size) * 100
                    bar_length = 30
                    filled = int(bar_length * downloaded / total_size)
                    bar = '█' * filled + '░' * (bar_length - filled)
                    print(f"\r    {Colors.OKCYAN}[{bar}] {percent:.1f}%{Colors.ENDC}",
                          end='', flush=True)

        print()
        file_size_mb = filepath.stat().st_size / (1024 * 1024)
        print_success(f"Downloaded {filename} ({file_size_mb:.2f} MB)")
        return filepath

    except requests.exceptions.Timeout:
        print_error("Download timed out")
    except requests.exceptions.RequestException as e:
        print_error(f"Download failed: {e}")
    except OSError as e:
        print_error(f"File write error: {e}")

    if filepath.exists():
        try:
            filepath.unlink()
        except OSError:
            pass

    return None


def cleanup_existing_extensions() -> None:
    """Remove existing Copilot extensions."""
    print_info("Checking for existing Copilot extensions...")

    try:
        result = subprocess.run(
            ['codium', '--list-extensions'],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )

        installed = [ext.strip() for ext in result.stdout.strip().split('\n') if ext.strip()]
        copilot_exts = [ext for ext in installed if 'copilot' in ext.lower()]

        if not copilot_exts:
            print_success("No existing Copilot extensions found")
            return

        print_warning(f"Found {len(copilot_exts)} existing Copilot extension(s)")
        for ext in copilot_exts:
            print_info(f"Uninstalling {ext}...", 4)
            subprocess.run(
                ['codium', '--uninstall-extension', ext],
                capture_output=True,
                check=True,
                timeout=30
            )

        print_success("Cleanup complete")

    except subprocess.TimeoutExpired:
        print_warning("Extension listing timed out")
    except subprocess.CalledProcessError as e:
        print_warning(f"Cleanup failed: {e}")
        print_info("Continuing anyway...", 4)


def terminate_codium() -> None:
    """Terminate all running VSCodium processes."""
    print_info("Checking for running VSCodium instances...")

    try:
        result = subprocess.run(
            ['pgrep', '-x', 'codium'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            print_success("No running VSCodium instances found")
            return

        pids = [pid.strip() for pid in result.stdout.strip().split('\n') if pid.strip()]
        print_warning(f"Found {len(pids)} running VSCodium process(es)")

        subprocess.run(['pkill', '-9', 'codium'], check=False, timeout=5)
        time.sleep(2)

        print_success("VSCodium processes terminated")

    except subprocess.TimeoutExpired:
        print_warning("Process termination timed out")
    except FileNotFoundError:
        print_warning("pgrep/pkill not available")


def install_extension(vsix_path: Path, extension_name: str) -> bool:
    """Install VSIX extension with retry logic."""
    print_info(f"Installing {extension_name}...")

    for attempt in range(1, INSTALL_RETRY_COUNT + 1):
        try:
            subprocess.run(
                ['codium', '--install-extension', str(vsix_path), '--force'],
                capture_output=True,
                text=True,
                check=True,
                timeout=90
            )
            print_success(f"Installed {extension_name}")
            return True

        except subprocess.TimeoutExpired:
            if attempt < INSTALL_RETRY_COUNT:
                print_warning(f"Installation timed out (attempt {attempt}/{INSTALL_RETRY_COUNT}), retrying...", 4)
                time.sleep(INSTALL_RETRY_DELAY)
            else:
                print_error("Installation timed out after all retries")

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() if e.stderr else str(e)

            if 'ScanningExtension' in error_msg:
                print_warning("Scanning error occurred, verifying installation...", 4)
                time.sleep(2)
                if is_extension_installed(extension_name):
                    print_success("Extension appears to be installed despite error")
                    return True

            if attempt < INSTALL_RETRY_COUNT:
                print_warning(f"Installation failed (attempt {attempt}/{INSTALL_RETRY_COUNT}), retrying...", 4)
                time.sleep(INSTALL_RETRY_DELAY)
            else:
                print_error(f"Installation failed: {error_msg}")

    return False


def is_extension_installed(extension_name: str) -> bool:
    """Check if a specific extension is installed."""
    try:
        result = subprocess.run(
            ['codium', '--list-extensions'],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )

        installed = [ext.strip().lower() for ext in result.stdout.strip().split('\n') if ext.strip()]
        return 'github.copilot-chat' in installed

    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return False


def validate_installation() -> bool:
    """Validate that Copilot Chat extension is installed."""
    print_info("Verifying installation...")

    try:
        result = subprocess.run(
            ['codium', '--list-extensions'],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )

        installed = [ext.strip() for ext in result.stdout.strip().split('\n') if ext.strip()]
        copilot_chat = [ext for ext in installed if 'github.copilot-chat' in ext.lower()]

        if copilot_chat:
            print_success("GitHub Copilot Chat is installed:")
            print_info(copilot_chat[0], 4)
            return True

        print_error("GitHub Copilot Chat extension not found")
        return False

    except subprocess.TimeoutExpired:
        print_error("Validation timed out")
    except subprocess.CalledProcessError as e:
        print_error(f"Validation failed: {e}")

    return False


def cleanup_files(files: List[Path]) -> None:
    """Remove downloaded VSIX files."""
    print_info("Cleaning up downloaded files...")

    for filepath in files:
        try:
            if filepath.exists():
                filepath.unlink()
                print_success(f"Removed {filepath.name}", 4)
        except OSError as e:
            print_warning(f"Could not remove {filepath.name}: {e}", 4)


# ============================================================================
# Main Application
# ============================================================================

def main() -> int:
    """Main application entry point."""
    total_steps = 10

    print_banner()

    # Step 0: Safety checks
    print_step(0, total_steps, "Safety Checks")
    check_not_running_in_codium()
    check_dependencies()
    print_success("Script is running in a safe environment")

    # Step 1: Get VSCodium version
    print_step(1, total_steps, "Detecting VSCodium Version")
    codium_version = get_codium_version()

    # Step 2: Detect supported API proposals
    print_step(2, total_steps, "Detecting Supported API Proposals")
    supported_apis = get_supported_api_proposals()

    # Step 3: Find compatible version
    print_step(3, total_steps, "Finding Compatible Extension")

    downloads: List[Tuple[Extension, CompatibleVersion]] = []

    for ext in sorted(EXTENSIONS, key=lambda x: x.install_order):
        print(f"\n  {Colors.BOLD}{ext.name}{Colors.ENDC}")

        ext_data = query_marketplace(ext)
        if not ext_data:
            continue

        compatible = find_compatible_version_with_api_check(
            ext_data, codium_version, ext.name, ext.extension_id, supported_apis
        )

        if compatible:
            downloads.append((ext, compatible))

    if len(downloads) != len(EXTENSIONS):
        missing = [e.name for e in EXTENSIONS if not any(d[0].name == e.name for d in downloads)]
        print_error(f"\nFailed to find compatible versions for: {', '.join(missing)}", 0)
        print_warning("Your VSCodium version may be too old for current Copilot extensions", 0)
        print_info("Try updating VSCodium to the latest version", 0)
        return 1

    print(f"\n{Colors.OKGREEN}{Colors.BOLD}✓ Compatible extension found!{Colors.ENDC}")

    # Step 4: Prepare VSCodium
    print_step(4, total_steps, "Preparing VSCodium")
    cleanup_existing_extensions()
    terminate_codium()

    # Step 5: Download extension
    print_step(5, total_steps, "Downloading Extension")
    downloaded_files: List[Tuple[Path, str, str]] = []

    for ext, compat_ver in downloads:
        filename = f"{ext.extension_id}-{compat_ver.version}.vsix"
        filepath = download_vsix(compat_ver.vsix_url, filename, ext.name)

        if filepath:
            downloaded_files.append((filepath, ext.name, ext.extension_id))
        else:
            print_error("\nDownload failed", 0)
            cleanup_files([f for f, _, _ in downloaded_files])
            return 1

    # Step 6: Extract API proposals
    print_step(6, total_steps, "Extracting API Proposals")
    extension_proposals: Dict[str, List[str]] = {}

    for filepath, ext_name, ext_id in downloaded_files:
        proposals = extract_api_proposals_from_vsix(filepath, ext_id)
        if proposals:
            extension_proposals[ext_id] = proposals
            print_info(f"Extracted {len(proposals)} API proposals from {ext_name}", 4)

    # Step 7: Update user product.json
    print_step(7, total_steps, "Configuring API Proposals")
    config_success = update_user_product_json(extension_proposals, supported_apis)

    # Step 8: Update user settings
    print_step(8, total_steps, "Configuring Settings")
    settings_success = update_user_settings()

    # Step 9: Install extension
    print_step(9, total_steps, "Installing Extension")
    install_success = True

    for filepath, ext_name, _ in downloaded_files:
        if not install_extension(filepath, ext_name):
            install_success = False

    # Step 10: Validate & Cleanup
    print_step(10, total_steps, "Validation & Cleanup")
    validation_ok = validate_installation()
    cleanup_files([f for f, _, _ in downloaded_files])

    # Final status
    print(f"\n{Colors.BOLD}{'═' * 62}{Colors.ENDC}")

    if validation_ok and config_success and install_success:
        print(f"{Colors.OKGREEN}{Colors.BOLD}✓ Installation Complete!{Colors.ENDC}\n")

        print()
        print(f"{Colors.BOLD}Installed Version:{Colors.ENDC}")
        for ext, compat_ver in downloads:
            print_info(f"{ext.name}: v{compat_ver.version}", 0)

        print()
        print(f"{Colors.BOLD}Configuration Summary:{Colors.ENDC}")
        print_info("✓ API Proposals: Configured", 0)
        print_info("✓ Model Selector: Enabled", 0)
        print_info("✓ Auto-Completions: Enabled", 0)
        print_info("✓ Settings: Updated", 0)

        print()
        print_info("Starting VSCodium...", 0)

        try:
            subprocess.Popen(
                ['codium'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        except OSError as e:
            print_warning(f"Could not start VSCodium: {e}", 0)
            print_info("Please start VSCodium manually", 0)

        print(f"\n{Colors.OKCYAN}✨ Enjoy GitHub Copilot in VSCodium! ✨{Colors.ENDC}")
        print(f"{Colors.OKCYAN}   • Inline completions{Colors.ENDC}")
        print(f"{Colors.OKCYAN}   • Chat with model selection{Colors.ENDC}")
        print(f"{Colors.OKCYAN}   • Full AI features enabled{Colors.ENDC}\n")

        return 0

    print(f"{Colors.FAIL}{Colors.BOLD}✗ Installation Incomplete{Colors.ENDC}\n")

    if not config_success:
        print_warning("API proposals were not configured", 0)

    if not settings_success:
        print_warning("Settings were not updated", 0)

    if not validation_ok:
        print_warning("Extension validation failed", 0)

    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n\n{Colors.WARNING}Installation cancelled by user{Colors.ENDC}\n")
        sys.exit(130)
    except Exception as e:
        print(f"\n{Colors.FAIL}Unexpected error: {e}{Colors.ENDC}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
