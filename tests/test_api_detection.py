"""
Tests for API proposal detection logic in the VSCodium Copilot installer.

These tests validate the core detection functions using simulated VSCodium
file content matching the versions the user is running (VSCodium 1.109.51242).
They do not require a real VSCodium installation.
"""

import json
import sys
import tempfile
from pathlib import Path
from typing import Set
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to import functions from the script (filename contains spaces/parens)
# ---------------------------------------------------------------------------
import importlib.util
import os

_SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'codium-copilot_Version13(1).py'
)

spec = importlib.util.spec_from_file_location('codium_copilot', _SCRIPT_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

get_runtime_api_proposals = mod.get_runtime_api_proposals
find_proposals_in_bundle_files = mod.find_proposals_in_bundle_files
find_runtime_proposals_file_dynamically = mod.find_runtime_proposals_file_dynamically
get_supported_api_proposals = mod.get_supported_api_proposals
check_api_compatibility = mod.check_api_compatibility
normalize_api_proposal = mod.normalize_api_proposal
is_version_compatible = mod.is_version_compatible


# ===========================================================================
# Fixtures – simulated VSCodium 1.109 file content
# ===========================================================================

# Representative excerpt of extensionApiProposals.js from VSCodium 1.109.
# Includes chatHooks (version 6) which is required by Copilot Chat ≥ 0.37.6.
PROPOSALS_JS_FORMATTED = """\
"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.allApiProposals = void 0;
const allApiProposals = Object.freeze({
    activeComment: {
        version: 1,
        proposal: 'declare module vscode {}'
    },
    aiRelatedInformation: {
        version: 1,
        proposal: 'declare module vscode {}'
    },
    chatHooks: {
        version: 6,
        proposal: 'declare module vscode {}'
    },
    chatParticipantPrivate: {
        version: 3,
        proposal: 'declare module vscode {}'
    },
    chatProvider: {
        version: 2,
        proposal: 'declare module vscode {}'
    },
    defaultChatParticipant: {
        version: 1,
        proposal: 'declare module vscode {}'
    },
    findFiles2: {
        version: 2,
        proposal: 'declare module vscode {}'
    },
    languageModelPicker: {
        version: 1,
        proposal: 'declare module vscode {}'
    },
    testingCoverage: {
        version: 1,
        proposal: 'declare module vscode {}'
    },
    inlineCompletionsAdditions: {
        version: 3,
        proposal: 'declare module vscode {}'
    },
    chatSessionsProvider: {
        version: 1,
        proposal: 'declare module vscode {}'
    },
});
exports.allApiProposals = allApiProposals;
"""

# Minified/bundled format with 25 entries (≥ 20 required to pass the bundle
# noise threshold) — as the proposals appear embedded inside
# workbench.desktop.main.js in package layouts that omit the standalone file.
_BUNDLE_PROPOSALS = (
    ['chatHooks:6', 'activeComment:1', 'aiRelatedInformation:1',
     'chatParticipantPrivate:3', 'chatProvider:2', 'defaultChatParticipant:1',
     'findFiles2:2', 'languageModelPicker:1', 'testingCoverage:1',
     'inlineCompletionsAdditions:3', 'chatSessionsProvider:1']
    + [f'bundleProposal{i}:{i}' for i in range(1, 15)]   # 14 extras = 25 total
)
PROPOSALS_JS_MINIFIED = (
    '"use strict";'
    'Object.defineProperty(exports,"__esModule",{value:true});'
    'const allApiProposals=Object.freeze({'
    + ','.join(f'{name.split(":")[0]}:{{version:{name.split(":")[1]},proposal:"..."}}'
               for name in _BUNDLE_PROPOSALS)
    + '});'
    'exports.allApiProposals=allApiProposals;'
)

# Proposals required by GitHub Copilot Chat 0.37.6–0.37.9 (the versions that
# the user expected to install but which were rejected).
COPILOT_CHAT_0_37_6_REQUIRED = [
    'activeComment@1',
    'aiRelatedInformation@1',
    'chatHooks@6',
    'chatParticipantPrivate@3',
    'chatProvider@2',
    'defaultChatParticipant@1',
    'findFiles2@2',
    'languageModelPicker@1',
]

# Proposals required by the older version that WAS accepted (0.37.5)
COPILOT_CHAT_0_37_5_REQUIRED = [
    'activeComment@1',
    'aiRelatedInformation@1',
    'chatParticipantPrivate@3',
    'chatProvider@2',
    'defaultChatParticipant@1',
    'findFiles2@2',
    'languageModelPicker@1',
]


# ===========================================================================
# 1. get_runtime_api_proposals
# ===========================================================================

class TestGetRuntimeApiProposals:
    """Tests for parsing extensionApiProposals.js content."""

    def _write_and_parse(self, content: str) -> Set[str]:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.js', delete=False, encoding='utf-8'
        ) as f:
            f.write(content)
            tmp = Path(f.name)
        try:
            result = get_runtime_api_proposals(tmp)
        finally:
            tmp.unlink(missing_ok=True)
        return result or set()

    def test_formatted_file_detects_chatHooks(self):
        proposals = self._write_and_parse(PROPOSALS_JS_FORMATTED)
        assert 'chatHooks' in proposals, (
            'chatHooks must be detected from a formatted extensionApiProposals.js '
            '(VSCodium 1.109 definitely implements chatHooks)'
        )

    def test_formatted_file_detects_all_expected_proposals(self):
        proposals = self._write_and_parse(PROPOSALS_JS_FORMATTED)
        expected = {
            'activeComment', 'aiRelatedInformation', 'chatHooks',
            'chatParticipantPrivate', 'chatProvider', 'defaultChatParticipant',
            'findFiles2', 'languageModelPicker', 'testingCoverage',
            'inlineCompletionsAdditions', 'chatSessionsProvider',
        }
        assert expected.issubset(proposals), (
            f'Missing proposals: {expected - proposals}'
        )

    def test_minified_file_detects_chatHooks(self):
        """Workbench bundle / minified format must also be parsed correctly."""
        proposals = self._write_and_parse(PROPOSALS_JS_MINIFIED)
        assert 'chatHooks' in proposals, (
            'chatHooks must be detected from a minified/bundled proposals source'
        )

    def test_minified_file_detects_all_expected_proposals(self):
        proposals = self._write_and_parse(PROPOSALS_JS_MINIFIED)
        expected = {
            'activeComment', 'aiRelatedInformation', 'chatHooks',
            'chatParticipantPrivate', 'chatProvider', 'defaultChatParticipant',
            'findFiles2', 'languageModelPicker', 'testingCoverage',
            'inlineCompletionsAdditions', 'chatSessionsProvider',
        }
        assert expected.issubset(proposals), (
            f'Missing proposals: {expected - proposals}'
        )

    def test_noise_words_excluded(self):
        """JS reserved words (version, exports, etc.) must not appear."""
        proposals = self._write_and_parse(PROPOSALS_JS_FORMATTED)
        noise = {'version', 'exports', 'module', 'define', 'require', 'default'}
        assert not (proposals & noise), (
            f'Noise words found in proposals: {proposals & noise}'
        )

    def test_nonexistent_file_returns_none(self):
        result = get_runtime_api_proposals(Path('/nonexistent/path/proposals.js'))
        assert result is None

    def test_empty_file_returns_none(self):
        result = self._write_and_parse('')
        # An empty file produces an empty set (falsy), not a valid proposals set
        assert not result

    def test_single_proposal_returns_it(self):
        """A file with a single valid proposal is parsed correctly.
        The noise threshold is applied only by find_proposals_in_bundle_files,
        not by get_runtime_api_proposals itself."""
        content = 'const x = { foo: { version: 1, proposal: "..." } };'
        result = self._write_and_parse(content)
        assert 'foo' in result

    def test_quoted_keys_detected(self):
        content_quoted = (
            'const x = Object.freeze({\n'
            + '\n'.join(
                f'    "proposal{i}": {{ version: {i}, proposal: "..." }},'
                for i in range(1, 25)
            )
            + '\n});'
        )
        proposals = self._write_and_parse(content_quoted)
        assert len(proposals) >= 20

    def test_returns_all_defined_proposals_for_valid_file(self):
        proposals = self._write_and_parse(PROPOSALS_JS_FORMATTED)
        assert len(proposals) >= 11  # we defined 11 proposals in fixture


# ===========================================================================
# 2. check_api_compatibility
# ===========================================================================

class TestCheckApiCompatibility:
    """Tests for the compatibility gate between a VSIX and the runtime."""

    def test_chatHooks6_compatible_when_present(self):
        """
        VSCodium 1.109 supports chatHooks.  An extension requiring chatHooks@6
        should be accepted when chatHooks is in the supported set.
        """
        supported = {'chatHooks', 'activeComment', 'aiRelatedInformation'}
        is_compat, unsupported = check_api_compatibility(
            ['chatHooks@6'], supported
        )
        assert is_compat
        assert unsupported == []

    def test_chatHooks6_incompatible_when_absent(self):
        """
        If the supported set does NOT include chatHooks, the extension must be
        rejected (this is the case when product.json fallback was used and
        chatHooks was missing from the allowlist).
        """
        supported = {'activeComment', 'aiRelatedInformation'}
        is_compat, unsupported = check_api_compatibility(
            ['chatHooks@6'], supported
        )
        assert not is_compat
        assert 'chatHooks@6' in unsupported

    def test_copilot_chat_0_37_6_compatible_with_vscodium_1_109_proposals(self):
        """
        Copilot Chat 0.37.6 requirements must all pass when VSCodium 1.109's
        full proposal set is available.
        """
        supported = {p.split('@')[0] for p in PROPOSALS_JS_FORMATTED.split('{')[0].split()}
        # Build from the formatted fixture
        supported_full: Set[str] = set()
        for line in PROPOSALS_JS_FORMATTED.splitlines():
            line = line.strip()
            if line and not line.startswith(('/', '*', '"', 'O', 'e', 'c')):
                name = line.split(':')[0].strip().strip('"\'')
                if name.isidentifier():
                    supported_full.add(name)
        # Use the actual parsed set for a definitive result
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.js', delete=False, encoding='utf-8'
        ) as f:
            f.write(PROPOSALS_JS_FORMATTED)
            tmp = Path(f.name)
        try:
            supported_parsed = get_runtime_api_proposals(tmp) or set()
        finally:
            tmp.unlink(missing_ok=True)

        is_compat, unsupported = check_api_compatibility(
            COPILOT_CHAT_0_37_6_REQUIRED, supported_parsed
        )
        assert is_compat, (
            f'Copilot Chat 0.37.6 should be compatible with VSCodium 1.109 proposals. '
            f'Unsupported: {unsupported}'
        )

    def test_copilot_chat_0_37_5_compatible(self):
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.js', delete=False, encoding='utf-8'
        ) as f:
            f.write(PROPOSALS_JS_FORMATTED)
            tmp = Path(f.name)
        try:
            supported_parsed = get_runtime_api_proposals(tmp) or set()
        finally:
            tmp.unlink(missing_ok=True)

        is_compat, unsupported = check_api_compatibility(
            COPILOT_CHAT_0_37_5_REQUIRED, supported_parsed
        )
        assert is_compat, f'Copilot Chat 0.37.5 must be compatible. Unsupported: {unsupported}'

    def test_empty_supported_set_accepts_everything(self):
        """
        When the supported set is empty (runtime file not found and we are in
        permissive mode), all proposals must be accepted.
        """
        is_compat, unsupported = check_api_compatibility(
            ['chatHooks@6', 'someUnknownProposal@99'], set()
        )
        assert is_compat
        assert unsupported == []

    def test_versioned_proposal_base_name_matching(self):
        """@N suffix is stripped; only the base name is matched against supported set."""
        supported = {'chatHooks'}
        is_compat, _ = check_api_compatibility(['chatHooks@6'], supported)
        assert is_compat

        is_compat2, _ = check_api_compatibility(['chatHooks@999'], supported)
        assert is_compat2

    def test_multiple_proposals_partial_match(self):
        """If even one proposal is unsupported, compatibility is False."""
        supported = {'chatHooks', 'activeComment'}
        is_compat, unsupported = check_api_compatibility(
            ['chatHooks@6', 'missingProposal@1', 'activeComment@1'], supported
        )
        assert not is_compat
        assert 'missingProposal@1' in unsupported
        assert 'chatHooks@6' not in unsupported


# ===========================================================================
# 3. normalize_api_proposal
# ===========================================================================

class TestNormalizeApiProposal:
    def test_strips_version_suffix(self):
        assert normalize_api_proposal('chatHooks@6') == 'chatHooks'

    def test_no_suffix_unchanged(self):
        assert normalize_api_proposal('chatHooks') == 'chatHooks'

    def test_multiple_at_signs(self):
        # Only the first @ split matters
        assert normalize_api_proposal('foo@1@2') == 'foo'


# ===========================================================================
# 4. is_version_compatible
# ===========================================================================

class TestIsVersionCompatible:
    """Version comparison tests using the exact versions from the user's run."""

    def test_vscodium_1109_satisfies_engine_1109(self):
        """VSCodium 1.109.51242 must satisfy engine requirement ^1.109.0-20260124."""
        assert is_version_compatible('1.109.51242', '^1.109.0-20260124')

    def test_vscodium_1109_does_not_satisfy_engine_1110(self):
        """VSCodium 1.109 must NOT satisfy an extension that requires 1.110+."""
        assert not is_version_compatible('1.109.51242', '^1.110.0')

    def test_exact_version_match(self):
        assert is_version_compatible('1.109.0', '^1.109.0')

    def test_older_vscodium_rejected(self):
        assert not is_version_compatible('1.100.0', '^1.109.0')

    def test_newer_vscodium_accepted(self):
        assert is_version_compatible('1.200.0', '^1.109.0')

    def test_tilde_prefix_stripped(self):
        assert is_version_compatible('1.109.51242', '~1.109.0')

    def test_gte_prefix_stripped(self):
        assert is_version_compatible('1.109.51242', '>=1.109.0')


# ===========================================================================
# 5. get_supported_api_proposals – integration tests with mock filesystem
# ===========================================================================

class TestGetSupportedApiProposals:
    """
    Integration tests that simulate different VSCodium installation layouts
    and verify that chatHooks is correctly detected (or that the permissive
    fallback is used when detection is impossible).
    """

    def test_standalone_proposals_file_found(self, tmp_path):
        """Primary path: extensionApiProposals.js exists and contains chatHooks."""
        proposals_dir = tmp_path / 'out' / 'vs' / 'workbench' / 'api' / 'common'
        proposals_dir.mkdir(parents=True)
        proposals_file = proposals_dir / 'extensionApiProposals.js'
        proposals_file.write_text(PROPOSALS_JS_FORMATTED, encoding='utf-8')

        # Patch RUNTIME_API_PROPOSALS_PATHS to point at our temp file
        with patch.object(mod, 'RUNTIME_API_PROPOSALS_PATHS', [proposals_file]):
            with patch.object(mod, 'WORKBENCH_BUNDLE_PATHS', []):
                result = get_supported_api_proposals()

        assert 'chatHooks' in result
        assert len(result) >= 11

    def test_bundle_file_fallback_when_standalone_missing(self, tmp_path):
        """
        Secondary path: extensionApiProposals.js is absent but the workbench
        bundle contains allApiProposals — chatHooks must still be detected.
        This simulates VSCodium 1.109 Debian packages that omit the standalone
        proposals file.
        """
        bundle_dir = tmp_path / 'out' / 'vs' / 'workbench'
        bundle_dir.mkdir(parents=True)
        bundle_file = bundle_dir / 'workbench.desktop.main.js'
        # Bundle must have >= 20 proposals to pass the noise threshold
        bundle_file.write_text(PROPOSALS_JS_MINIFIED, encoding='utf-8')

        with patch.object(mod, 'RUNTIME_API_PROPOSALS_PATHS', []):
            with patch.object(mod, 'WORKBENCH_BUNDLE_PATHS', [bundle_file]):
                with patch.object(mod, 'SYSTEM_PRODUCT_JSON_PATHS', []):
                    result = get_supported_api_proposals()

        assert 'chatHooks' in result
        assert len(result) >= 20

    def test_permissive_fallback_when_no_source_found(self, tmp_path):
        """
        Fallback path: neither the standalone file nor the bundle exists.
        The function must return an empty set (permissive mode) so that API
        compatibility checks are skipped — avoiding false rejections like the
        chatHooks@6 issue reported by the user.
        """
        with patch.object(mod, 'RUNTIME_API_PROPOSALS_PATHS', []):
            with patch.object(mod, 'WORKBENCH_BUNDLE_PATHS', []):
                with patch.object(mod, 'SYSTEM_PRODUCT_JSON_PATHS', []):
                    result = get_supported_api_proposals()

        assert result == set(), (
            'Permissive fallback must return empty set so no extensions are '
            'incorrectly rejected when detection sources are unavailable'
        )

    def test_product_json_only_does_not_block_chatHooks(self, tmp_path):
        """
        Regression test for the original bug: when product.json is the only
        source and chatHooks is absent from extensionEnabledApiProposals, the
        script must NOT reject extensions that require chatHooks.

        The fixed code returns an empty set (permissive) rather than the
        incomplete product.json allowlist.
        """
        # Create a product.json that mirrors the user's system:
        # 117 proposals listed but chatHooks NOT among them.
        product_json = tmp_path / 'product.json'
        fake_proposals = {f'ext{i}': [f'proposal{j}@1' for j in range(5)] for i in range(24)}
        # Ensure chatHooks is absent
        product_data = {'extensionEnabledApiProposals': fake_proposals}
        product_json.write_text(json.dumps(product_data), encoding='utf-8')

        with patch.object(mod, 'RUNTIME_API_PROPOSALS_PATHS', []):
            with patch.object(mod, 'WORKBENCH_BUNDLE_PATHS', []):
                with patch.object(mod, 'SYSTEM_PRODUCT_JSON_PATHS', [product_json]):
                    result = get_supported_api_proposals()

        # Result must be empty (permissive) — NOT the product.json allowlist
        assert result == set(), (
            'Must return empty set (permissive) when runtime file is absent, '
            'not the incomplete product.json allowlist that omits chatHooks'
        )

        # Verify that with permissive result, chatHooks@6 is accepted
        is_compat, unsupported = check_api_compatibility(['chatHooks@6'], result)
        assert is_compat, (
            'chatHooks@6 must be accepted in permissive mode '
            '(empty supported set)'
        )

    def test_chatHooks_accepted_end_to_end_with_proposals_file(self, tmp_path):
        """
        End-to-end scenario: VSCodium 1.109 layout with proposals file.
        Copilot Chat 0.37.6 (requires chatHooks@6) must be accepted.
        """
        proposals_dir = tmp_path / 'out' / 'vs' / 'workbench' / 'api' / 'common'
        proposals_dir.mkdir(parents=True)
        proposals_file = proposals_dir / 'extensionApiProposals.js'
        proposals_file.write_text(PROPOSALS_JS_FORMATTED, encoding='utf-8')

        with patch.object(mod, 'RUNTIME_API_PROPOSALS_PATHS', [proposals_file]):
            with patch.object(mod, 'WORKBENCH_BUNDLE_PATHS', []):
                supported = get_supported_api_proposals()

        is_compat, unsupported = check_api_compatibility(
            COPILOT_CHAT_0_37_6_REQUIRED, supported
        )
        assert is_compat, (
            f'Copilot Chat 0.37.6 must be accepted when VSCodium 1.109 '
            f'proposals are detected. Unsupported APIs: {unsupported}'
        )

    def test_chatHooks_accepted_end_to_end_with_bundle_file(self, tmp_path):
        """
        End-to-end scenario: bundle-only layout (no standalone proposals file).
        Copilot Chat 0.37.6 must still be accepted via bundle detection.
        """
        bundle_dir = tmp_path / 'out' / 'vs' / 'workbench'
        bundle_dir.mkdir(parents=True)
        bundle_file = bundle_dir / 'workbench.desktop.main.js'
        # Bundle must have >= 20 proposals to pass the noise threshold
        bundle_file.write_text(PROPOSALS_JS_MINIFIED, encoding='utf-8')

        with patch.object(mod, 'RUNTIME_API_PROPOSALS_PATHS', []):
            with patch.object(mod, 'WORKBENCH_BUNDLE_PATHS', [bundle_file]):
                with patch.object(mod, 'SYSTEM_PRODUCT_JSON_PATHS', []):
                    supported = get_supported_api_proposals()

        is_compat, unsupported = check_api_compatibility(
            COPILOT_CHAT_0_37_6_REQUIRED, supported
        )
        assert is_compat, (
            f'Copilot Chat 0.37.6 must be accepted via bundle detection. '
            f'Unsupported APIs: {unsupported}'
        )

    def test_chatHooks_accepted_end_to_end_permissive(self):
        """
        End-to-end scenario: no detection sources at all (permissive mode).
        Copilot Chat 0.37.6 must be accepted.
        """
        with patch.object(mod, 'RUNTIME_API_PROPOSALS_PATHS', []):
            with patch.object(mod, 'WORKBENCH_BUNDLE_PATHS', []):
                with patch.object(mod, 'SYSTEM_PRODUCT_JSON_PATHS', []):
                    supported = get_supported_api_proposals()

        is_compat, unsupported = check_api_compatibility(
            COPILOT_CHAT_0_37_6_REQUIRED, supported
        )
        assert is_compat, (
            f'Copilot Chat 0.37.6 must be accepted in permissive mode. '
            f'Unsupported: {unsupported}'
        )


# ===========================================================================
# 6. find_proposals_in_bundle_files
# ===========================================================================

class TestFindProposalsInBundleFiles:
    def test_finds_chatHooks_from_bundle(self, tmp_path):
        # The bundle must contain >= 20 proposals to pass the noise threshold
        bundle_file = tmp_path / 'workbench.desktop.main.js'
        bundle_file.write_text(PROPOSALS_JS_MINIFIED, encoding='utf-8')

        with patch.object(mod, 'WORKBENCH_BUNDLE_PATHS', [bundle_file]):
            result = find_proposals_in_bundle_files()
        assert result is not None
        assert 'chatHooks' in result

    def test_finds_chatHooks_from_install_root(self, tmp_path):
        # Bundle placed at resources/app/out/vs/workbench/workbench.desktop.main.js
        bundle_dir = tmp_path / 'out' / 'vs' / 'workbench'
        bundle_dir.mkdir(parents=True)
        bundle_file = bundle_dir / 'workbench.desktop.main.js'
        bundle_file.write_text(PROPOSALS_JS_MINIFIED, encoding='utf-8')

        with patch.object(mod, 'WORKBENCH_BUNDLE_PATHS', []):
            result = find_proposals_in_bundle_files([tmp_path])
        assert result is not None
        assert 'chatHooks' in result

    def test_returns_none_when_no_bundle_exists(self, tmp_path):
        with patch.object(mod, 'WORKBENCH_BUNDLE_PATHS', []):
            result = find_proposals_in_bundle_files([tmp_path])
        assert result is None

    def test_returns_none_when_bundle_too_small(self, tmp_path):
        # A bundle with fewer than 20 proposals is treated as noise
        bundle_file = tmp_path / 'workbench.desktop.main.js'
        bundle_file.write_text(
            'const x = {foo: {version: 1, proposal: "..."}}', encoding='utf-8'
        )

        with patch.object(mod, 'WORKBENCH_BUNDLE_PATHS', [bundle_file]):
            result = find_proposals_in_bundle_files()
        assert result is None
