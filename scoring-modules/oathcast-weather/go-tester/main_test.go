package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/tetratelabs/wazero"
	"github.com/tetratelabs/wazero/api"
	"golang.org/x/crypto/sha3"
)

const (
	maxWASMBytes        = 32 * 1024 * 1024
	maxLinearMemoryPage = 64
	maxQuestionBytes    = 8 * 1024
	maxGroundTruthBytes = 8 * 1024
	maxMinerAnswerBytes = 4 * 1024
)

func repoRoot(t *testing.T) string {
	t.Helper()
	root, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	return root
}

func wasmPath(t *testing.T) string {
	t.Helper()
	return filepath.Join(
		repoRoot(t), "scoring-modules", "oathcast-weather", "rust-module", "target",
		"wasm32-unknown-unknown", "release", "oathcast_weather_scorer.wasm",
	)
}

func assertSignature(t *testing.T, definition api.FunctionDefinition, params, results []api.ValueType) {
	t.Helper()
	if definition == nil {
		t.Fatal("missing required function export")
	}
	if !sameTypes(definition.ParamTypes(), params) || !sameTypes(definition.ResultTypes(), results) {
		t.Fatalf(
			"unexpected signature for %s: params=%v results=%v",
			definition.DebugName(), definition.ParamTypes(), definition.ResultTypes(),
		)
	}
}

func sameTypes(left, right []api.ValueType) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}

func TestArtifactContract(t *testing.T) {
	bytes, err := os.ReadFile(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	evidence := loadReleaseEvidence(t)
	if evidence.SchemaVersion != "oathcast_weather_wasm_release_evidence_v7" {
		t.Fatalf("unexpected release evidence schema %q", evidence.SchemaVersion)
	}
	if evidence.Status != "probability_scan_fix_local_candidate_after_registration_41_stage_2_rejection" {
		t.Fatalf("unexpected release evidence status %q", evidence.Status)
	}
	if !evidence.Artifact.RegistrationCandidate {
		t.Fatal("rank-only artifact must be marked as a registration candidate")
	}
	if evidence.Artifact.ByteSize != 42790 ||
		evidence.Artifact.SHA256 != "2c1f7ad3ec409d91a778a3d49a6d554de09bc12701834fd859f07591550a0774" ||
		evidence.Artifact.Keccak256RawBytes != "0xe217913a8a22b2d80b607008b3605e45b646e624b56005f1df84925e9818e47a" {
		t.Fatalf("unexpected local candidate evidence: %+v", evidence.Artifact)
	}
	if evidence.Fixture.Path != "fixtures/wasm_scoring_cases.json" {
		t.Fatalf("unexpected fixture path %q", evidence.Fixture.Path)
	}
	fixtureBytes, err := os.ReadFile(filepath.Join(repoRoot(t), evidence.Fixture.Path))
	if err != nil {
		t.Fatal(err)
	}
	fixtureDigest := sha256.Sum256(fixtureBytes)
	if actual := hex.EncodeToString(fixtureDigest[:]); actual != evidence.Fixture.SHA256 {
		t.Fatalf("fixture SHA-256 is %s, release evidence pins %s", actual, evidence.Fixture.SHA256)
	}
	if evidence.Build.IsolatedCleanBuilds != 2 || !evidence.Build.ByteIdentical ||
		len(evidence.Build.IsolatedBuildSHA256) != 2 {
		t.Fatalf("unexpected isolated build evidence: %+v", evidence.Build)
	}
	for index, digest := range evidence.Build.IsolatedBuildSHA256 {
		if digest != evidence.Artifact.SHA256 {
			t.Fatalf(
				"isolated build %d SHA-256 is %s, want artifact SHA-256 %s",
				index+1, digest, evidence.Artifact.SHA256,
			)
		}
	}
	if evidence.RegisteredArtifact.ByteSize != 16292 ||
		evidence.RegisteredArtifact.SHA256 != "97d481b724bd79fa78d32218f20be9c1b85468109a8ff2a0da2d2574c775f3af" ||
		evidence.RegisteredArtifact.Keccak256RawBytes != "0xea169bc97fc43c3de086d26765714a28c909d29a6d79181f93d2f9e236776ab8" ||
		evidence.RegisteredArtifact.PinataGatewayURL != "https://gateway.pinata.cloud/ipfs/QmSww9z6Dp1LPitKj3HsTRY8pjNNzhwvDLiAufKxskA3P1" ||
		evidence.RegisteredArtifact.DropboxURL != "https://www.dropbox.com/scl/fi/27orv68frtedmkqq1t9wt/oathcast_weather_scorer.wasm?rlkey=9mrm44geuaejp1629zdntfng3&st=sigr9vji&dl=1" ||
		!slices.Equal(evidence.RegisteredArtifact.RegistrationIDs, []int{7, 19}) ||
		evidence.RegisteredArtifact.Stage2RegistrationID != 19 {
		t.Fatalf("unexpected registered artifact evidence: %+v", evidence.RegisteredArtifact)
	}
	if evidence.Artifact.SHA256 == evidence.RegisteredArtifact.SHA256 ||
		evidence.Artifact.Keccak256RawBytes == evidence.RegisteredArtifact.Keccak256RawBytes {
		t.Fatal("the local candidate and the registration 19 artifact must remain distinct")
	}
	// Registration 41 is settled on-chain, so its bytes are pinned to their own
	// literals rather than to the local candidate. The candidate has since moved
	// ahead of it and must be asserted distinct, exactly as it is against
	// registration 19 above.
	if evidence.Registration41Artifact.ByteSize != 42798 ||
		evidence.Registration41Artifact.SHA256 != "4c3e91ac887abf492cbc662a2d02e0b0bae906a176b2ae4b7bf986419a2db174" ||
		evidence.Registration41Artifact.Keccak256RawBytes != "0xd8b298ded6e50a69fd6cc79350a819536927d879c81250924689edbea98517f8" ||
		evidence.Registration41Artifact.DropboxURL != "https://www.dropbox.com/scl/fi/27orv68frtedmkqq1t9wt/oathcast_weather_scorer.wasm?rlkey=9mrm44geuaejp1629zdntfng3&st=jwhbk80f&dl=1" ||
		evidence.Registration41Artifact.RegistrationID != 41 ||
		evidence.Registration41Artifact.Intent != "WEATHER_FORECAST" ||
		evidence.Registration41Artifact.IntentID != "0x9eefcfc9ee9243dea613f4a518d6a4602dfacbd6ad1efe17f9239824a69a034e" ||
		!evidence.Registration41Artifact.HostedBytesVerified {
		t.Fatalf("unexpected registration 41 artifact evidence: %+v", evidence.Registration41Artifact)
	}
	if evidence.Artifact.SHA256 == evidence.Registration41Artifact.SHA256 ||
		evidence.Artifact.Keccak256RawBytes == evidence.Registration41Artifact.Keccak256RawBytes {
		t.Fatal("the local candidate must be distinct from the registered registration 41 artifact")
	}
	if evidence.CurrentArtifactABI.FunctionExportCount != 3 {
		t.Fatalf("release evidence pins %d function exports, want 3", evidence.CurrentArtifactABI.FunctionExportCount)
	}
	if evidence.CurrentArtifactABI.BreakdownAnswerExported {
		t.Fatal("release evidence must not claim a breakdown_answer export")
	}
	wantGateOrder := []string{
		"complete_local_verification_of_new_hash",
		"new_complete_wallet_wrapper_and_nested_call_preflight",
		"fresh_explicit_user_authorization",
		"single_new_registration_and_stage_2_result",
	}
	if !slices.Equal(evidence.RegistrationBoundary.ExternalGateOrder, wantGateOrder) {
		t.Fatalf(
			"unexpected external gate order %v, want %v",
			evidence.RegistrationBoundary.ExternalGateOrder, wantGateOrder,
		)
	}
	wantCompletedGates := []string{
		"live_validator_rank_only_rollout_confirmation",
		"explicit_current_provisional_registration_authorization",
		"portal_transaction_confirmed",
		"telegraph_registry_and_intent_fix_confirmation",
		"live_portal_corrected_registry_and_intent_abi_observed",
		"existing_cid_independently_byte_verified",
		"corrected_inner_call_simulated",
		"second_portal_retry_authorization_consumed",
		"second_portal_retry_transaction_confirmed",
		"second_postflight_registry_reads_completed",
		"corrected_wallet_wrapper_and_nested_call_decoded",
		"corrected_current_registry_transaction_confirmed",
		"current_registry_registration_id_7_observed",
		"exact_registered_artifact_and_weather_forecast_binding_verified",
		"registration_id_19_current_registry_transaction_confirmed",
		"registration_id_19_validator_ingestion_observed",
		"registration_id_19_stage_1_pass_team_confirmed_and_champion_comparison_correlated",
		"registration_id_19_stage_2_rejection_observed",
		"stage_2_factual_paraphrase_rules_clarified",
		"new_local_candidate_reproducible_build_verified",
		"registration_41_hosted_bytes_verified",
		"registration_41_manual_authorization_consumed",
		"registration_id_41_current_registry_transaction_confirmed",
		"registration_id_41_validator_ingestion_observed",
		"registration_id_41_stage_1_pass_inferred_from_champion_comparison",
		"registration_id_41_stage_2_rejection_observed",
	}
	if !slices.Equal(evidence.RegistrationBoundary.CompletedExternalGates, wantCompletedGates) {
		t.Fatalf(
			"unexpected completed external gates %v, want %v",
			evidence.RegistrationBoundary.CompletedExternalGates,
			wantCompletedGates,
		)
	}
	if !evidence.RegistrationBoundary.LiveValidatorRankOnlyRolloutConfirmed {
		t.Fatal("release evidence must record Telegraph's rank-only validator fix confirmation")
	}
	if evidence.RegistrationBoundary.CurrentExternalGate != "stop_no_further_registration_authorized" {
		t.Fatalf(
			"unexpected current external gate %q",
			evidence.RegistrationBoundary.CurrentExternalGate,
		)
	}
	if !evidence.RegistrationBoundary.Uploaded || !evidence.RegistrationBoundary.HostedBytesVerified ||
		evidence.RegistrationBoundary.HostedBytesVerificationSource == nil ||
		evidence.RegistrationBoundary.HostedGatewayURL == nil ||
		*evidence.RegistrationBoundary.HostedGatewayURL != evidence.Registration41Artifact.DropboxURL ||
		evidence.RegistrationBoundary.HostedIPFSCID != nil {
		t.Fatal("registration 41 must retain verified Dropbox-hosted byte evidence")
	}
	if !evidence.RegistrationBoundary.TransactionSubmitted || !evidence.RegistrationBoundary.TransactionConfirmed ||
		evidence.RegistrationBoundary.CorrectedTransactionSubmitted ||
		evidence.RegistrationBoundary.CorrectedTransactionConfirmed ||
		evidence.RegistrationBoundary.CorrectedTransactionEffectiveRegistrationObserved ||
		!evidence.RegistrationBoundary.TransactionEffectiveRegistrationObserved {
		t.Fatal("registration 41 must retain a confirmed effective current-registry transaction")
	}
	if !evidence.RegistrationBoundary.HistoricalFirstTransactionConfirmed ||
		evidence.RegistrationBoundary.HistoricalFirstTransactionEffectiveRegistrationObserved ||
		!evidence.RegistrationBoundary.HistoricalSecondTransactionConfirmed ||
		evidence.RegistrationBoundary.HistoricalSecondTransactionEffectiveRegistrationObserved {
		t.Fatal("release evidence must retain the obsolete-registry transaction history")
	}
	if !evidence.RegistrationBoundary.ExplicitUserAuthorizationReceived ||
		!evidence.RegistrationBoundary.HistoricalRetryAuthorizationReceived ||
		!evidence.RegistrationBoundary.HistoricalRetryAuthorizationConsumed ||
		!evidence.RegistrationBoundary.CorrectedRegistrationAuthorizationConsumed ||
		!evidence.RegistrationBoundary.FreshReregistrationAuthorizationReceived ||
		!evidence.RegistrationBoundary.Registration41AuthorizationConsumed ||
		evidence.RegistrationBoundary.FurtherRegistrationAuthorized {
		t.Fatal("release evidence must record consumed registration 41 authorization and block another attempt")
	}
	if evidence.RegistrationBoundary.RegistrationMode != "registered_artifact_id_41_stage_2_rejected" {
		t.Fatalf("unexpected registration mode %q", evidence.RegistrationBoundary.RegistrationMode)
	}
	if evidence.RegistrationBoundary.IntentBindingStatus != "registration_id_41_weather_forecast_binding_observed" {
		t.Fatalf("unexpected Intent binding status %q", evidence.RegistrationBoundary.IntentBindingStatus)
	}
	if !evidence.RegistrationBoundary.ValidatorStage1Observed ||
		evidence.RegistrationBoundary.ValidatorStage1Passed == nil ||
		!*evidence.RegistrationBoundary.ValidatorStage1Passed ||
		evidence.RegistrationBoundary.ReportedIntentThresholdResultObserved ||
		evidence.RegistrationBoundary.ReportedIntentThresholdPassed != nil ||
		!evidence.RegistrationBoundary.ValidatorStage2Observed ||
		evidence.RegistrationBoundary.ValidatorStage2Promoted == nil ||
		*evidence.RegistrationBoundary.ValidatorStage2Promoted {
		t.Fatal("release evidence must record registration 41 Stage 1 pass and Stage 2 rejection")
	}
	if !evidence.RegistrationBoundary.RequiresSeparateAuthorization ||
		evidence.RegistrationBoundary.ExternalGateOrderScope != "42,798-byte factual-paraphrase revision culminating in registration 41" ||
		evidence.RegistrationBoundary.NextTransactionalGate != "none_without_fresh_complete_preflight_and_fresh_explicit_user_authorization" {
		t.Fatalf("unexpected registration 41 authorization boundary: %+v", evidence.RegistrationBoundary)
	}
	followUp := evidence.AuthoritySnapshots.TelegraphTeamClarifications.FollowUp20260815
	if !followUp.ValidatorFixReported || !followUp.LiveRetryInvited {
		t.Fatal("release evidence must retain Telegraph's validator fix and retry invitation")
	}
	if !followUp.PortalRetryTransactionPerformed || followUp.AuthoritativeValidatorProcessingObserved {
		t.Fatal("release evidence must distinguish the confirmed portal retry from unobserved validator processing")
	}
	if followUp.NodeLogRootCause != "module[env] not instantiated" {
		t.Fatalf("unexpected Telegraph node-log root cause %q", followUp.NodeLogRootCause)
	}
	fix := evidence.AuthoritySnapshots.TelegraphTeamClarifications.IntentRegistryFix20260815
	if !fix.IntentBindingReportedFixed || !fix.RegistryMismatchReportedFixed ||
		!fix.LivePortalFixObserved || !fix.ReregistrationInvited || fix.HistoricalRegistration5Migrated {
		t.Fatalf("unexpected Telegraph Intent/registry fix evidence: %+v", fix)
	}
	if fix.ReportedMinimumIntentScore != 0.6 || fix.ScoreAggregationFormulaDocumented {
		t.Fatalf("unexpected reported Intent threshold evidence: %+v", fix)
	}
	indexing := evidence.AuthoritySnapshots.TelegraphTeamClarifications.PostRegistrationIndexing20260816
	if !indexing.CurrentRegistryRegistration7ReportedConfirmed ||
		!indexing.IPFSGatewayTimeoutReportedAsIndexingCause ||
		!indexing.IndexingFixPRReportedMerged ||
		!indexing.DashboardStillEmptyAfterReportedMerge ||
		!indexing.AnotherReregistrationSuggested ||
		indexing.NewPreflightObserved || indexing.NewTransactionObserved ||
		indexing.FreshUserAuthorizationReceived {
		t.Fatalf("unexpected post-registration indexing guidance: %+v", indexing)
	}
	clarification := evidence.AuthoritySnapshots.TelegraphTeamClarifications.Stage2RejectionAndFixtureClarification20260817
	if clarification.RegistrationID != 19 ||
		clarification.Wallet != "0x7dc9C9D535B68C3c6273e3323f0e52E5851C3278" ||
		clarification.ActivationStatus != "rejected" || !clarification.Stage1Passed ||
		!strings.Contains(clarification.Stage1ConfirmationSource, "User-relayed Telegraph team confirmation") ||
		clarification.CandidateWins != 31 || clarification.ChampionWins != 32 ||
		clarification.ComparableCases != 32 || clarification.CandidateMargin != 0.31248063 ||
		clarification.ChampionMargin != 0.37360683 || clarification.HistoricalRowsEvaluated != 0 ||
		clarification.FixtureCategory != "factual paraphrase and lexical discrimination" ||
		!slices.Equal(clarification.ExcludedFixtureCategories, []string{"numeric", "time_window", "json"}) ||
		clarification.ReportedPairMarginFloor != 0.15 || clarification.ReportedNearMissCases != 6 ||
		clarification.ReportedPromotionRule != "promotion is automatic after all six near-miss cases pass; there is no direct candidate-margin-versus-champion-margin comparison" ||
		clarification.Reported060Metric != "Spearman rank correlation between the candidate and the live champion's historical scores" ||
		clarification.HiddenPairScoresDisclosed {
		t.Fatalf("unexpected registration 19 fixture clarification: %+v", clarification)
	}
	if evidence.Verification.RustNativeTestsPassed != 40 ||
		evidence.Verification.PythonRepositoryTestsPassed != 422 ||
		evidence.Verification.SyntheticFactualPairCount != 88 ||
		evidence.Verification.SyntheticFactualMinimumMargin != 0.20625 ||
		evidence.Verification.SyntheticFactualOrdinalSpearman != 0.959566 {
		t.Fatalf("unexpected local factual-paraphrase verification: %+v", evidence.Verification)
	}
	postflight := evidence.Verification.HistoricalFirstRegistrationPostflight
	if postflight.TransactionHash != "0x82db3d5ade954cf4995cbc01ed4f2a0a3b24c352b0ce9efa15ceb1f18d7d7471" {
		t.Fatalf("unexpected registration transaction %q", postflight.TransactionHash)
	}
	if !postflight.TransactionConfirmed || postflight.TransactionStatus != 1 || postflight.ChainID != 84532 {
		t.Fatalf("unexpected transaction confirmation state: %+v", postflight)
	}
	if postflight.OuterTo != "0xdb9b1e94b5b69df7e401ddbede43491141047db3" ||
		postflight.OuterSelector != "0xcef6d209" ||
		postflight.OuterSignature != "redeemDelegations(bytes[],bytes32[],bytes[])" {
		t.Fatalf("unexpected delegation wrapper: to=%q selector=%q signature=%q", postflight.OuterTo, postflight.OuterSelector, postflight.OuterSignature)
	}
	if postflight.InnerTarget != "0xac683bFa8F1C892E23e8300d14c20678C6FC0CA3" {
		t.Fatalf("unexpected portal transaction target %q", postflight.InnerTarget)
	}
	if postflight.InnerValueWei != "0" || postflight.InnerSelector != "0x19238d1c" || postflight.InnerSignature != "registerWasm(bytes32 wasmHash, string wasmUrl, string[] whitelistedUrls)" {
		t.Fatalf("unexpected nested registration call: value=%q selector=%q signature=%q", postflight.InnerValueWei, postflight.InnerSelector, postflight.InnerSignature)
	}
	if postflight.InnerWASMURL != evidence.RegisteredArtifact.PinataGatewayURL {
		t.Fatalf("transaction WASM URL %q does not match registered artifact %q", postflight.InnerWASMURL, evidence.RegisteredArtifact.PinataGatewayURL)
	}
	if postflight.InnerWASMHash != evidence.RegisteredArtifact.Keccak256RawBytes {
		t.Fatalf("transaction WASM hash %q does not match registered artifact %q", postflight.InnerWASMHash, evidence.RegisteredArtifact.Keccak256RawBytes)
	}
	if postflight.EventEmitter != postflight.InnerTarget || postflight.EventRegistrationID != 5 {
		t.Fatalf("unexpected registration event: emitter=%q id=%d", postflight.EventEmitter, postflight.EventRegistrationID)
	}
	if len(postflight.InnerWhitelistedURLs) != 0 {
		t.Fatalf("transaction whitelist must be empty, got %v", postflight.InnerWhitelistedURLs)
	}
	if postflight.PortalDashboardWASMCount != 0 || postflight.ValidatorIndexedRegistryRegistration5Present || postflight.PortalTargetRegistryRegistration5Present {
		t.Fatal("release evidence must preserve the unindexed registration-5 result")
	}
	if postflight.Classification != "confirmed_event_on_portal_configured_unindexed_registry" {
		t.Fatalf("unexpected postflight classification %q", postflight.Classification)
	}
	portal := evidence.AuthoritySnapshots.TelegraphIntegrationPortal
	if portal.LiveDeploymentRegistryAddress != evidence.AuthoritySnapshots.DeployedRegistry.Address ||
		!portal.LiveDeploymentMatchesValidatorIndexedRegistry {
		t.Fatal("current portal deployment must match the validator-indexed registry")
	}
	if portal.CurrentRegisterWASMSelector != "0xfe1e40f7" ||
		portal.CurrentRegisterWASMSignature != "registerWasm(bytes32 wasmHash, string wasmUrl, string intent)" {
		t.Fatalf("unexpected current portal registration ABI: %+v", portal)
	}
	if portal.HistoricalFailedAttemptRegistryAddress != postflight.InnerTarget {
		t.Fatal("historical portal target must remain tied to the failed attempt")
	}
	if portal.RepositoryEnvExampleRegistryAddress != postflight.InnerTarget ||
		portal.DashboardValidatorAPICommit != "1ff2f7db1139657aff8f9073cac34e61c91cbef2" ||
		portal.DashboardRegistrationAPIUpstream != "VALIDATOR_BASE_URL/engine/validator/v1/addresses/{address}" ||
		!portal.WriteReadConfigurationSourcesDistinct {
		t.Fatalf("portal write/read configuration split drifted: %+v", portal)
	}
	deployed := evidence.AuthoritySnapshots.DeployedRegistry
	if !deployed.Registration5Present || deployed.Registration5CandidateMatch ||
		deployed.Registration6CandidateMatch || deployed.CandidatePresentBeforeRetry {
		t.Fatal("current registry entries 5 and 6 must remain distinct from the OathCast candidate")
	}
	if deployed.WASMEntityCountAfterCorrectedRegistration != 7 ||
		!deployed.Registration7Present || !deployed.Registration7CandidateMatch ||
		!deployed.CandidatePresentAfterCorrectedRegistration || deployed.GetWASM7RawZero ||
		!deployed.GetWASM7ContainsWallet || !deployed.GetWASM7ContainsHash ||
		!deployed.GetWASM7ContainsGatewayURL || !deployed.GetWASM7ContainsIntent {
		t.Fatalf("current registry must contain the exact OathCast ID 7 entry: %+v", deployed)
	}
	if evidence.AuthoritySnapshots.PortalTargetRegistry.Registration5Present {
		t.Fatal("historical portal target must not expose registration 5")
	}
	if evidence.AuthoritySnapshots.DeployedRegistry.Address != "0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8" ||
		evidence.AuthoritySnapshots.PortalTargetRegistry.Address != postflight.InnerTarget {
		t.Fatalf("registry address reconciliation drifted: validator=%q portal=%q", evidence.AuthoritySnapshots.DeployedRegistry.Address, evidence.AuthoritySnapshots.PortalTargetRegistry.Address)
	}
	second := evidence.Verification.HistoricalSecondRegistrationPostflight
	if second.TransactionHash != "0xde08c7a66627b98cf1a55fc7a3b4d2e8065b08d9b20d09af5c015852faa140d1" {
		t.Fatalf("unexpected second registration transaction %q", second.TransactionHash)
	}
	if !second.TransactionConfirmed || second.TransactionStatus != 1 || second.ChainID != 84532 ||
		second.BlockNumber != 45530303 || second.BlockTimestamp != "2026-08-15T21:21:34Z" || second.GasUsed != 431377 {
		t.Fatalf("unexpected second transaction receipt: %+v", second)
	}
	if second.OuterFrom != "0xb42f812a44c22cc6b861478900401ee759ebead6" ||
		second.OuterTo != "0xdb9b1e94b5b69df7e401ddbede43491141047db3" ||
		second.OuterSelector != "0xcef6d209" ||
		second.OuterSignature != "redeemDelegations(bytes[],bytes32[],bytes[])" ||
		second.DelegatedWallet != "0x6D4192Bca39641F9aA22DB17EfF991D6adD005dE" {
		t.Fatalf("unexpected second delegation wrapper: %+v", second)
	}
	if second.InnerTarget != "0xac683bFa8F1C892E23e8300d14c20678C6FC0CA3" || second.InnerValueWei != "0" ||
		second.InnerSelector != "0x19238d1c" ||
		second.InnerSignature != "registerWasm(bytes32 wasmHash, string wasmUrl, string[] whitelistedUrls)" {
		t.Fatalf("unexpected second nested registration call: %+v", second)
	}
	if second.InnerWASMHash != evidence.RegisteredArtifact.Keccak256RawBytes ||
		second.InnerWASMURL != evidence.RegisteredArtifact.PinataGatewayURL || len(second.InnerWhitelistedURLs) != 0 {
		t.Fatalf("unexpected second candidate packet: %+v", second)
	}
	if second.EventEmitter != second.InnerTarget || second.EventRegistrationID != 7 ||
		second.EventIntentID != "0xc626be4c56e7581efef0fcde650a04cbb189bc294398958914f3ef201fcc6827" ||
		second.EventEntityType != 2 {
		t.Fatalf("unexpected second registration event: %+v", second)
	}
	if second.PortalDashboardWASMCount != 0 ||
		second.RegistryObservation.CorrectedRegistry.Address != evidence.AuthoritySnapshots.DeployedRegistry.Address ||
		second.RegistryObservation.CorrectedRegistry.EntityCountType2 != 6 ||
		!second.RegistryObservation.CorrectedRegistry.GetWASM7RawZero ||
		second.RegistryObservation.OldRegistry.Address != second.InnerTarget ||
		second.RegistryObservation.OldRegistry.EntityCountType2 != 0 ||
		!second.RegistryObservation.OldRegistry.GetWASM7RawZero {
		t.Fatalf("unexpected second registry observation: %+v", second.RegistryObservation)
	}
	if second.Classification != "confirmed_event_on_obsolete_unindexed_registry_after_live_portal_path_split" {
		t.Fatalf("unexpected second postflight classification %q", second.Classification)
	}
	if second.EvidenceArtifact != "artifacts/registration-drafts/oathcast-weather-wasm-reregistration-postflight-2026-08-15T212134Z.json" {
		t.Fatalf("unexpected second postflight artifact %q", second.EvidenceArtifact)
	}
	preflight := evidence.Verification.HistoricalCorrectedInnerCallPreflight
	if preflight.ChainID != 84532 || preflight.From != "0x6D4192Bca39641F9aA22DB17EfF991D6adD005dE" ||
		preflight.Target != evidence.AuthoritySnapshots.DeployedRegistry.Address || preflight.ValueWei != "0" {
		t.Fatalf("unexpected corrected preflight envelope: %+v", preflight)
	}
	if preflight.Selector != "0xfe1e40f7" ||
		preflight.Signature != "registerWasm(bytes32 wasmHash, string wasmUrl, string intent)" ||
		preflight.WASMHash != evidence.RegisteredArtifact.Keccak256RawBytes ||
		preflight.WASMURL != evidence.RegisteredArtifact.PinataGatewayURL ||
		preflight.Intent != "WEATHER_FORECAST" {
		t.Fatalf("unexpected corrected inner call: %+v", preflight)
	}
	if !preflight.InnerCallSimulationSucceeded || preflight.ProspectiveRegistrationID != 7 ||
		preflight.OuterWalletWrapperDecoded || preflight.TransactionBroadcast {
		t.Fatalf("unexpected corrected preflight state: %+v", preflight)
	}
	corrected := evidence.Verification.CorrectedRegistrationPostflight
	if corrected.TransactionHash != "0x3997dfd5b514cf56b434fb4a475e6cc015e5ae9d42064073ff044bc4f67be51e" ||
		!corrected.TransactionConfirmed || corrected.TransactionStatus != 1 || corrected.ChainID != 84532 ||
		corrected.BlockNumber != 45541793 || corrected.BlockTimestamp != "2026-08-16T03:44:34Z" ||
		corrected.GasUsed != 445007 || corrected.NativeValueWei != "0" {
		t.Fatalf("unexpected corrected transaction receipt: %+v", corrected)
	}
	if corrected.OuterFrom != "0xc066ac5D385419B1A8c43a0E146fA439837a8B8" ||
		corrected.OuterTo != "0xdb9b1e94b5b69df7e401ddbede43491141047db3" ||
		corrected.OuterSelector != "0xcef6d209" ||
		corrected.OuterSignature != "redeemDelegations(bytes[],bytes32[],bytes[])" ||
		corrected.DelegatedWallet != "0x6D4192Bca39641F9aA22DB17EfF991D6adD005dE" {
		t.Fatalf("unexpected corrected delegation wrapper: %+v", corrected)
	}
	if corrected.InnerTarget != evidence.AuthoritySnapshots.DeployedRegistry.Address ||
		corrected.InnerValueWei != "0" || corrected.InnerSelector != "0xfe1e40f7" ||
		corrected.InnerSignature != "registerWasm(bytes32 wasmHash, string wasmUrl, string intent)" ||
		corrected.InnerWASMHash != evidence.RegisteredArtifact.Keccak256RawBytes ||
		corrected.InnerWASMURL != evidence.RegisteredArtifact.PinataGatewayURL ||
		corrected.InnerIntent != "WEATHER_FORECAST" {
		t.Fatalf("unexpected corrected nested registration call: %+v", corrected)
	}
	if corrected.EventEmitter != corrected.InnerTarget || corrected.EventRegistrationID != 7 ||
		corrected.EventRegistrant != corrected.DelegatedWallet || corrected.EventEntityType != 2 ||
		corrected.EventIntentID != "0x9eefcfc9ee9243dea613f4a518d6a4602dfacbd6ad1efe17f9239824a69a034e" ||
		corrected.EventContentHash != evidence.RegisteredArtifact.Keccak256RawBytes ||
		corrected.EventContentURL != evidence.RegisteredArtifact.PinataGatewayURL {
		t.Fatalf("unexpected corrected registration event: %+v", corrected)
	}
	if corrected.RegistryObservation.Address != corrected.InnerTarget ||
		corrected.RegistryObservation.EntityCountType2 != 7 ||
		corrected.RegistryObservation.GetWASM7RawZero ||
		!corrected.RegistryObservation.GetWASM7ContainsWallet ||
		!corrected.RegistryObservation.GetWASM7ContainsHash ||
		!corrected.RegistryObservation.GetWASM7ContainsGatewayURL ||
		!corrected.RegistryObservation.GetWASM7ContainsIntent ||
		corrected.PortalDashboardWASMCount != 0 {
		t.Fatalf("unexpected corrected registry or Dashboard observation: %+v", corrected)
	}
	if corrected.Classification != "correct_registry_registration_event_dashboard_unindexed_validator_unobserved" ||
		corrected.EvidenceArtifact != "artifacts/registration-drafts/oathcast-weather-wasm-corrected-postflight-2026-08-16T034434Z.json" {
		t.Fatalf("unexpected corrected postflight classification: %+v", corrected)
	}
	stage2 := evidence.Verification.Registration19Stage2Result
	if stage2.RegistrationID != evidence.RegisteredArtifact.Stage2RegistrationID ||
		stage2.AuthorAddress != "0x7dc9c9d535b68c3c6273e3323f0e52e5851c3278" ||
		stage2.TransactionHash != "0xa6bc6f653eec4a5c79acac4a6e747222d48fd257367c325cd0e6c0090d321e73" ||
		stage2.ChainID != 84532 || stage2.BlockNumber != 45577927 ||
		stage2.BlockTimestamp != "2026-08-16T23:49:02Z" || stage2.TransactionStatus != 1 ||
		stage2.GasUsed != 486373 || stage2.NativeValueWei != "0" {
		t.Fatalf("unexpected registration 19 transaction evidence: %+v", stage2)
	}
	if stage2.RegistryAddress != evidence.AuthoritySnapshots.DeployedRegistry.Address ||
		stage2.WASMURL != evidence.RegisteredArtifact.DropboxURL ||
		stage2.WASMKeccak256RawBytes != evidence.RegisteredArtifact.Keccak256RawBytes ||
		stage2.Intent != "WEATHER_FORECAST" ||
		stage2.IntentID != "0x9eefcfc9ee9243dea613f4a518d6a4602dfacbd6ad1efe17f9239824a69a034e" {
		t.Fatalf("registration 19 must reference the registered artifact and Intent: %+v", stage2)
	}
	if stage2.ActivationStatus != "rejected" || !stage2.Stage1Passed ||
		stage2.EvalScore != 0.31248063 || stage2.EvalDetails.CandidateMargin != 0.31248063 ||
		stage2.EvalDetails.ChampionMargin != 0.37360683 || stage2.EvalDetails.CandidateWins != 31 ||
		stage2.EvalDetails.ChampionWins != 32 || stage2.EvalDetails.ComparableCases != 32 ||
		stage2.EvalDetails.WorstSelfMatch != 1 || stage2.EvalDetails.ScoreStddev != 0.26765382 ||
		stage2.EvalDetails.HistoricalRowsEvaluated != 0 {
		t.Fatalf("unexpected registration 19 Stage 1/Stage 2 result: %+v", stage2)
	}
	if stage2.RejectionReason != "lost to the current champion on ordering: your scorer ranked the good answer above the bad one on fewer fixture cases than the champion (you: 31 of 32, champion: 32 of 32). Score correct answers above wrong ones more consistently." {
		t.Fatalf("unexpected registration 19 rejection reason %q", stage2.RejectionReason)
	}
	registration41 := evidence.Verification.Registration41Stage2Result
	if registration41.RegistrationID != evidence.Registration41Artifact.RegistrationID ||
		registration41.AuthorAddress != "0x7dc9C9D535B68C3c6273e3323f0e52E5851C3278" ||
		registration41.TransactionHash != "0x4bfdc7a894ca55edbb18c18cd5ee79b32673c8b3f5b8d04ab6bc5e48a458ccf8" ||
		registration41.Chain != "Base Sepolia" || registration41.ChainID != 84532 ||
		registration41.BlockNumber != 45613554 || registration41.BlockTimestamp != "2026-08-17T19:36:36Z" ||
		registration41.TransactionStatus != 1 || registration41.GasUsed != 476373 ||
		registration41.ExplorerURL != "https://base-sepolia.blockscout.com/tx/0x4bfdc7a894ca55edbb18c18cd5ee79b32673c8b3f5b8d04ab6bc5e48a458ccf8" ||
		registration41.NativeValueWei != "0" {
		t.Fatalf("unexpected registration 41 transaction evidence: %+v", registration41)
	}
	if registration41.OuterFrom != "0xB01caEa8c6C47bbf4F4b4c5080Ca642043359C2E" ||
		registration41.OuterTo != "0xdb9B1e94B5b69Df7e401DDbedE43491141047dB3" ||
		registration41.OuterSelector != "0xcef6d209" ||
		registration41.OuterSignature != "redeemDelegations(bytes[],bytes32[],bytes[])" ||
		registration41.DelegatedWallet != registration41.AuthorAddress ||
		registration41.RegistryAddress != evidence.AuthoritySnapshots.DeployedRegistry.Address ||
		registration41.InnerTarget != registration41.RegistryAddress ||
		registration41.InnerValueWei != "0" || registration41.InnerSelector != "0xfe1e40f7" ||
		registration41.InnerSignature != "registerWasm(bytes32 wasmHash, string wasmUrl, string intent)" ||
		registration41.RegistrationSignature != "registerWasm(bytes32,string,string)" {
		t.Fatalf("unexpected registration 41 wallet packet: %+v", registration41)
	}
	if registration41.WASMURL != evidence.Registration41Artifact.DropboxURL ||
		registration41.WASMByteSize != evidence.Registration41Artifact.ByteSize ||
		registration41.WASMSHA256 != evidence.Registration41Artifact.SHA256 ||
		registration41.WASMKeccak256RawBytes != evidence.Registration41Artifact.Keccak256RawBytes ||
		!registration41.HostedBytesVerified || registration41.Intent != evidence.Registration41Artifact.Intent ||
		registration41.IntentID != evidence.Registration41Artifact.IntentID {
		t.Fatalf("registration 41 must reference the exact hosted artifact and Intent: %+v", registration41)
	}
	event41 := registration41.RegistrationEvent
	if event41.Emitter != registration41.RegistryAddress || event41.RegistrationID != registration41.RegistrationID ||
		event41.Registrant != registration41.DelegatedWallet || event41.EntityType != 2 ||
		event41.IntentID != registration41.IntentID || event41.ContentURL != registration41.WASMURL ||
		event41.ContentHash != registration41.WASMKeccak256RawBytes ||
		event41.Event != "IntentRegistered(uint256,address,uint8,bytes32,string,bytes32)" {
		t.Fatalf("unexpected registration 41 event evidence: %+v", event41)
	}
	if registration41.ActivationStatus != "rejected" || !registration41.Stage1Passed || registration41.Stage2Promoted ||
		registration41.EvalScore != 0.37852418 || registration41.EvalDetails.CandidateMargin != 0.37852418 ||
		registration41.EvalDetails.ChampionMargin != 0.37360683 || registration41.EvalDetails.CandidateWins != 31 ||
		registration41.EvalDetails.ChampionWins != 32 || registration41.EvalDetails.ComparableCases != 32 ||
		registration41.EvalDetails.WorstSelfMatch != 1 || registration41.EvalDetails.ScoreStddev != 0.29563302 ||
		registration41.EvalDetails.HistoricalRowsEvaluated != 0 || registration41.HiddenPairScoresDisclosed {
		t.Fatalf("unexpected registration 41 Stage 1/Stage 2 result: %+v", registration41)
	}
	if registration41.RegisteredAtValidator != "2026-08-17T19:37:35.066237Z" ||
		registration41.UpdatedAtValidator != "2026-08-17T19:38:20.299494Z" ||
		registration41.SourceURL != "https://integrate.telegraphprotocol.com/api/registrations/0x7dc9C9D535B68C3c6273e3323f0e52E5851C3278" ||
		registration41.Stage1Evidence != "The validator reached champion comparison; no separate Stage 1 API field was retained." ||
		registration41.RejectionReason != "lost to the current champion on ordering: candidate won 31 of 32 comparable cases while the champion won 32 of 32." ||
		!registration41.ManualAuthorizationConsumed || registration41.FurtherRegistrationAuthorized ||
		registration41.EvidenceArtifact != "artifacts/registration-drafts/oathcast-weather-wasm-registration-41-postflight-2026-08-17T193636Z.json" {
		t.Fatalf("unexpected registration 41 postflight boundary: %+v", registration41)
	}
	if len(bytes) != evidence.Artifact.ByteSize {
		t.Fatalf(
			"WASM is %d bytes, release evidence pins %d",
			len(bytes), evidence.Artifact.ByteSize,
		)
	}
	digest := sha256.Sum256(bytes)
	if actual := hex.EncodeToString(digest[:]); actual != evidence.Artifact.SHA256 {
		t.Fatalf("WASM SHA-256 is %s, release evidence pins %s", actual, evidence.Artifact.SHA256)
	}
	keccak := sha3.NewLegacyKeccak256()
	if _, err := keccak.Write(bytes); err != nil {
		t.Fatal(err)
	}
	if actual := "0x" + hex.EncodeToString(keccak.Sum(nil)); actual != evidence.Artifact.Keccak256RawBytes {
		t.Fatalf(
			"WASM raw-byte Keccak-256 is %s, release evidence pins %s",
			actual, evidence.Artifact.Keccak256RawBytes,
		)
	}
	if len(bytes) > maxWASMBytes {
		t.Fatalf("WASM is %d bytes, above the 32 MiB limit", len(bytes))
	}
	ctx := context.Background()
	runtime := wazero.NewRuntime(ctx)
	defer runtime.Close(ctx)
	compiled, err := runtime.CompileModule(ctx, bytes)
	if err != nil {
		t.Fatal(err)
	}
	defer compiled.Close(ctx)

	if imports := compiled.ImportedFunctions(); len(imports) != 0 {
		t.Fatalf("unexpected function imports: %v", imports)
	}
	if imports := compiled.ImportedMemories(); len(imports) != 0 {
		t.Fatalf("unexpected memory imports: %v", imports)
	}
	if hasImportSection, err := hasSection(bytes, 2); err != nil {
		t.Fatal(err)
	} else if hasImportSection {
		t.Fatal("WASM must not contain an import section")
	}
	exports := compiled.ExportedFunctions()
	if len(exports) != 3 {
		t.Fatalf("expected exactly three function exports, got %v", exports)
	}
	i32 := api.ValueTypeI32
	f32 := api.ValueTypeF32
	assertSignature(t, exports["alloc"], []api.ValueType{i32}, []api.ValueType{i32})
	assertSignature(t, exports["dealloc"], []api.ValueType{i32, i32}, nil)
	assertSignature(
		t, exports["rank_answer"],
		[]api.ValueType{i32, i32, i32, i32, i32, i32},
		[]api.ValueType{f32},
	)
	memories := compiled.ExportedMemories()
	if len(memories) != 1 || memories["memory"] == nil {
		t.Fatalf("expected exactly one exported linear memory named memory, got %v", memories)
	}
	if maxPages, hasMax := memories["memory"].Max(); !hasMax || maxPages != maxLinearMemoryPage {
		t.Fatalf(
			"exported memory maximum is pages=%d declared=%v, want exactly %d pages (4 MiB)",
			maxPages, hasMax, maxLinearMemoryPage,
		)
	}
	exportNames, err := exportNames(bytes)
	if err != nil {
		t.Fatal(err)
	}
	if len(exportNames) != 6 {
		t.Fatalf("expected documented ABI plus two Rust linker globals, got %v", exportNames)
	}
	wantedExports := map[string]bool{
		"memory": true, "alloc": true, "dealloc": true, "rank_answer": true,
		"__data_end": true, "__heap_base": true,
	}
	for _, name := range exportNames {
		if !wantedExports[name] {
			t.Fatalf("unexpected export %q in %v", name, exportNames)
		}
	}
	if hasStart, err := hasSection(bytes, 8); err != nil {
		t.Fatal(err)
	} else if hasStart {
		t.Fatal("WASM must not contain a start section")
	}
}

type scoreExpectation struct {
	Exact *float32 `json:"exact"`
	Min   *float32 `json:"min"`
	Max   *float32 `json:"max"`
}

type repeatValue struct {
	Value string `json:"value"`
	Count int    `json:"count"`
}

type fixtureCase struct {
	CaseID        string           `json:"case_id"`
	Question      string           `json:"question"`
	GroundTruth   string           `json:"ground_truth"`
	MinerAnswer   *string          `json:"miner_answer"`
	MinerRepeat   *repeatValue     `json:"miner_answer_repeat"`
	Expected      scoreExpectation `json:"expected_score"`
	OrderingGroup string           `json:"ordering_group"`
	QualityRank   int              `json:"quality_rank"`
}

type factualPair struct {
	PairID          string   `json:"pair_id"`
	Split           string   `json:"split"`
	Category        string   `json:"category"`
	Question        string   `json:"question"`
	GroundTruth     string   `json:"ground_truth"`
	GoodAnswer      string   `json:"good_answer"`
	BadAnswer       string   `json:"bad_answer"`
	MinimumMargin   float32  `json:"minimum_margin"`
	MaximumBadScore *float32 `json:"maximum_bad_score"`
}

type fixtureFile struct {
	Cases        []fixtureCase `json:"cases"`
	FactualPairs []factualPair `json:"factual_pairs"`
}

type releaseEvidence struct {
	SchemaVersion string `json:"schema_version"`
	Status        string `json:"status"`
	Artifact      struct {
		ByteSize              int    `json:"byte_size"`
		SHA256                string `json:"sha256"`
		Keccak256RawBytes     string `json:"keccak256_raw_bytes"`
		RegistrationCandidate bool   `json:"registration_candidate"`
	} `json:"artifact"`
	Registration41Artifact struct {
		ByteSize                      int    `json:"byte_size"`
		SHA256                        string `json:"sha256"`
		Keccak256RawBytes             string `json:"keccak256_raw_bytes"`
		DropboxURL                    string `json:"dropbox_url"`
		RegistrationID                int    `json:"registration_id"`
		Intent                        string `json:"intent"`
		IntentID                      string `json:"intent_id"`
		HostedBytesVerified           bool   `json:"hosted_bytes_verified"`
		HostedBytesVerificationMethod string `json:"hosted_bytes_verification_method"`
	} `json:"registration_41_artifact"`
	RegisteredArtifact struct {
		ByteSize             int    `json:"byte_size"`
		SHA256               string `json:"sha256"`
		Keccak256RawBytes    string `json:"keccak256_raw_bytes"`
		PinataGatewayURL     string `json:"pinata_gateway_url"`
		DropboxURL           string `json:"dropbox_url"`
		RegistrationIDs      []int  `json:"registration_ids"`
		Stage2RegistrationID int    `json:"stage_2_registration_id"`
	} `json:"registered_artifact"`
	Fixture struct {
		Path   string `json:"path"`
		SHA256 string `json:"sha256"`
	} `json:"fixture"`
	Build struct {
		IsolatedCleanBuilds int      `json:"isolated_clean_builds"`
		ByteIdentical       bool     `json:"byte_identical"`
		IsolatedBuildSHA256 []string `json:"isolated_build_sha256"`
	} `json:"build"`
	CurrentArtifactABI struct {
		FunctionExportCount     int  `json:"function_export_count"`
		BreakdownAnswerExported bool `json:"breakdown_answer_exported"`
	} `json:"current_artifact_abi"`
	AuthoritySnapshots struct {
		TelegraphIntegrationPortal struct {
			LiveDeploymentRegistryAddress                 string `json:"live_deployment_registry_address"`
			LiveDeploymentMatchesValidatorIndexedRegistry bool   `json:"live_deployment_matches_validator_indexed_registry"`
			HistoricalFailedAttemptRegistryAddress        string `json:"historical_failed_attempt_registry_address"`
			RepositoryEnvExampleRegistryAddress           string `json:"repository_env_example_registry_address"`
			DashboardRegistrationAPIUpstream              string `json:"dashboard_registration_api_upstream"`
			DashboardValidatorAPICommit                   string `json:"dashboard_validator_api_commit"`
			WriteReadConfigurationSourcesDistinct         bool   `json:"write_read_configuration_sources_distinct"`
			CurrentRegisterWASMSelector                   string `json:"current_register_wasm_selector"`
			CurrentRegisterWASMSignature                  string `json:"current_register_wasm_signature"`
		} `json:"telegraph_integration_portal"`
		DeployedRegistry struct {
			Address                                    string `json:"address"`
			Registration5Present                       bool   `json:"registration_5_present"`
			Registration5CandidateMatch                bool   `json:"registration_5_candidate_match"`
			Registration6CandidateMatch                bool   `json:"registration_6_candidate_match"`
			CandidatePresentBeforeRetry                bool   `json:"candidate_present_before_retry"`
			WASMEntityCountAfterCorrectedRegistration  int    `json:"wasm_entity_count_after_corrected_registration"`
			Registration7Present                       bool   `json:"registration_7_present"`
			Registration7CandidateMatch                bool   `json:"registration_7_candidate_match"`
			CandidatePresentAfterCorrectedRegistration bool   `json:"candidate_present_after_corrected_registration"`
			GetWASM7RawZero                            bool   `json:"get_wasm_7_raw_zero"`
			GetWASM7ContainsWallet                     bool   `json:"get_wasm_7_contains_wallet"`
			GetWASM7ContainsHash                       bool   `json:"get_wasm_7_contains_hash"`
			GetWASM7ContainsGatewayURL                 bool   `json:"get_wasm_7_contains_gateway_url"`
			GetWASM7ContainsIntent                     bool   `json:"get_wasm_7_contains_intent"`
		} `json:"deployed_registry"`
		PortalTargetRegistry struct {
			Address              string `json:"address"`
			Registration5Present bool   `json:"registration_5_present"`
		} `json:"portal_target_registry"`
		TelegraphTeamClarifications struct {
			FollowUp20260815 struct {
				ValidatorFixReported                     bool   `json:"validator_fix_reported"`
				LiveRetryInvited                         bool   `json:"live_retry_invited"`
				PortalRetryTransactionPerformed          bool   `json:"portal_retry_transaction_performed"`
				AuthoritativeValidatorProcessingObserved bool   `json:"authoritative_validator_processing_observed"`
				NodeLogRootCause                         string `json:"node_log_root_cause"`
			} `json:"follow_up_2026_08_15"`
			IntentRegistryFix20260815 struct {
				IntentBindingReportedFixed        bool    `json:"intent_binding_reported_fixed"`
				RegistryMismatchReportedFixed     bool    `json:"registry_mismatch_reported_fixed"`
				LivePortalFixObserved             bool    `json:"live_portal_fix_observed"`
				ReregistrationInvited             bool    `json:"reregistration_invited"`
				HistoricalRegistration5Migrated   bool    `json:"historical_registration_5_migrated"`
				ReportedMinimumIntentScore        float64 `json:"reported_minimum_intent_score"`
				ScoreAggregationFormulaDocumented bool    `json:"score_aggregation_formula_documented"`
			} `json:"intent_registry_fix_2026_08_15"`
			PostRegistrationIndexing20260816 struct {
				CurrentRegistryRegistration7ReportedConfirmed bool `json:"current_registry_registration_7_reported_confirmed"`
				IPFSGatewayTimeoutReportedAsIndexingCause     bool `json:"ipfs_gateway_timeout_reported_as_indexing_cause"`
				IndexingFixPRReportedMerged                   bool `json:"indexing_fix_pr_reported_merged"`
				DashboardStillEmptyAfterReportedMerge         bool `json:"dashboard_still_empty_after_reported_merge"`
				AnotherReregistrationSuggested                bool `json:"another_reregistration_suggested"`
				NewPreflightObserved                          bool `json:"new_preflight_observed"`
				NewTransactionObserved                        bool `json:"new_transaction_observed"`
				FreshUserAuthorizationReceived                bool `json:"fresh_user_authorization_received"`
			} `json:"post_registration_indexing_2026_08_16"`
			Stage2RejectionAndFixtureClarification20260817 struct {
				RegistrationID            int      `json:"registration_id"`
				Wallet                    string   `json:"wallet"`
				ActivationStatus          string   `json:"activation_status"`
				Stage1Passed              bool     `json:"stage_1_passed"`
				Stage1ConfirmationSource  string   `json:"stage_1_confirmation_source"`
				CandidateWins             int      `json:"candidate_wins"`
				ChampionWins              int      `json:"champion_wins"`
				ComparableCases           int      `json:"comparable_cases"`
				CandidateMargin           float64  `json:"candidate_margin"`
				ChampionMargin            float64  `json:"champion_margin"`
				HistoricalRowsEvaluated   int      `json:"historical_rows_evaluated"`
				FixtureCategory           string   `json:"fixture_category"`
				ExcludedFixtureCategories []string `json:"excluded_fixture_categories"`
				ReportedPairMarginFloor   float64  `json:"reported_pair_margin_floor"`
				ReportedNearMissCases     int      `json:"reported_near_miss_cases"`
				ReportedPromotionRule     string   `json:"reported_promotion_rule"`
				Reported060Metric         string   `json:"reported_0_60_metric"`
				HiddenPairScoresDisclosed bool     `json:"hidden_pair_scores_disclosed"`
			} `json:"stage_2_rejection_and_fixture_clarification_2026_08_17"`
		} `json:"telegraph_team_clarifications"`
	} `json:"authority_snapshots"`
	Verification struct {
		RustNativeTestsPassed                 int     `json:"rust_native_tests_passed"`
		PythonRepositoryTestsPassed           int     `json:"python_repository_tests_passed"`
		SyntheticFactualPairCount             int     `json:"synthetic_factual_pair_count"`
		SyntheticFactualMinimumMargin         float64 `json:"synthetic_factual_minimum_margin"`
		SyntheticFactualOrdinalSpearman       float64 `json:"synthetic_factual_ordinal_spearman"`
		HistoricalFirstRegistrationPostflight struct {
			TransactionHash                              string   `json:"transaction_hash"`
			TransactionConfirmed                         bool     `json:"transaction_confirmed"`
			TransactionStatus                            int      `json:"transaction_status"`
			ChainID                                      int      `json:"chain_id"`
			OuterTo                                      string   `json:"outer_to"`
			OuterSelector                                string   `json:"outer_selector"`
			OuterSignature                               string   `json:"outer_signature"`
			InnerTarget                                  string   `json:"inner_target"`
			InnerValueWei                                string   `json:"inner_value_wei"`
			InnerSelector                                string   `json:"inner_selector"`
			InnerSignature                               string   `json:"inner_signature"`
			InnerWASMHash                                string   `json:"inner_wasm_hash"`
			InnerWASMURL                                 string   `json:"inner_wasm_url"`
			InnerWhitelistedURLs                         []string `json:"inner_whitelisted_urls"`
			EventEmitter                                 string   `json:"event_emitter"`
			EventRegistrationID                          int      `json:"event_registration_id"`
			PortalDashboardWASMCount                     int      `json:"portal_dashboard_wasm_count"`
			ValidatorIndexedRegistryRegistration5Present bool     `json:"validator_indexed_registry_registration_5_present"`
			PortalTargetRegistryRegistration5Present     bool     `json:"portal_target_registry_registration_5_present"`
			Classification                               string   `json:"classification"`
		} `json:"historical_first_registration_postflight"`
		HistoricalSecondRegistrationPostflight struct {
			TransactionHash          string   `json:"transaction_hash"`
			TransactionConfirmed     bool     `json:"transaction_confirmed"`
			TransactionStatus        int      `json:"transaction_status"`
			ChainID                  int      `json:"chain_id"`
			BlockNumber              int      `json:"block_number"`
			BlockTimestamp           string   `json:"block_timestamp"`
			GasUsed                  int      `json:"gas_used"`
			OuterFrom                string   `json:"outer_from"`
			OuterTo                  string   `json:"outer_to"`
			OuterSelector            string   `json:"outer_selector"`
			OuterSignature           string   `json:"outer_signature"`
			DelegatedWallet          string   `json:"delegated_wallet"`
			InnerTarget              string   `json:"inner_target"`
			InnerValueWei            string   `json:"inner_value_wei"`
			InnerSelector            string   `json:"inner_selector"`
			InnerSignature           string   `json:"inner_signature"`
			InnerWASMHash            string   `json:"inner_wasm_hash"`
			InnerWASMURL             string   `json:"inner_wasm_url"`
			InnerWhitelistedURLs     []string `json:"inner_whitelisted_urls"`
			EventEmitter             string   `json:"event_emitter"`
			EventRegistrationID      int      `json:"event_registration_id"`
			EventIntentID            string   `json:"event_intent_id"`
			EventEntityType          int      `json:"event_entity_type"`
			PortalDashboardWASMCount int      `json:"portal_dashboard_wasm_count"`
			RegistryObservation      struct {
				CorrectedRegistry struct {
					Address          string `json:"address"`
					EntityCountType2 int    `json:"entity_count_type_2"`
					GetWASM7RawZero  bool   `json:"get_wasm_7_raw_zero"`
				} `json:"corrected_registry"`
				OldRegistry struct {
					Address          string `json:"address"`
					EntityCountType2 int    `json:"entity_count_type_2"`
					GetWASM7RawZero  bool   `json:"get_wasm_7_raw_zero"`
				} `json:"old_registry"`
			} `json:"registry_observation"`
			Classification   string `json:"classification"`
			EvidenceArtifact string `json:"evidence_artifact"`
		} `json:"historical_second_registration_postflight"`
		HistoricalCorrectedInnerCallPreflight struct {
			ChainID                      int    `json:"chain_id"`
			From                         string `json:"from"`
			Target                       string `json:"target"`
			ValueWei                     string `json:"value_wei"`
			Selector                     string `json:"selector"`
			Signature                    string `json:"signature"`
			WASMHash                     string `json:"wasm_hash"`
			WASMURL                      string `json:"wasm_url"`
			Intent                       string `json:"intent"`
			InnerCallSimulationSucceeded bool   `json:"inner_call_simulation_succeeded"`
			ProspectiveRegistrationID    int    `json:"prospective_registration_id"`
			OuterWalletWrapperDecoded    bool   `json:"outer_wallet_wrapper_decoded"`
			TransactionBroadcast         bool   `json:"transaction_broadcast"`
		} `json:"historical_corrected_inner_call_preflight"`
		CorrectedRegistrationPostflight struct {
			TransactionHash      string `json:"transaction_hash"`
			TransactionConfirmed bool   `json:"transaction_confirmed"`
			TransactionStatus    int    `json:"transaction_status"`
			ChainID              int    `json:"chain_id"`
			BlockNumber          int    `json:"block_number"`
			BlockTimestamp       string `json:"block_timestamp"`
			GasUsed              int    `json:"gas_used"`
			NativeValueWei       string `json:"native_value_wei"`
			OuterFrom            string `json:"outer_from"`
			OuterTo              string `json:"outer_to"`
			OuterSelector        string `json:"outer_selector"`
			OuterSignature       string `json:"outer_signature"`
			DelegatedWallet      string `json:"delegated_wallet"`
			InnerTarget          string `json:"inner_target"`
			InnerValueWei        string `json:"inner_value_wei"`
			InnerSelector        string `json:"inner_selector"`
			InnerSignature       string `json:"inner_signature"`
			InnerWASMHash        string `json:"inner_wasm_hash"`
			InnerWASMURL         string `json:"inner_wasm_url"`
			InnerIntent          string `json:"inner_intent"`
			EventEmitter         string `json:"event_emitter"`
			EventRegistrationID  int    `json:"event_registration_id"`
			EventRegistrant      string `json:"event_registrant"`
			EventIntentID        string `json:"event_intent_id"`
			EventEntityType      int    `json:"event_entity_type"`
			EventContentURL      string `json:"event_content_url"`
			EventContentHash     string `json:"event_content_hash"`
			RegistryObservation  struct {
				Address                    string `json:"address"`
				EntityCountType2           int    `json:"entity_count_type_2"`
				GetWASM7RawZero            bool   `json:"get_wasm_7_raw_zero"`
				GetWASM7ContainsWallet     bool   `json:"get_wasm_7_contains_wallet"`
				GetWASM7ContainsHash       bool   `json:"get_wasm_7_contains_hash"`
				GetWASM7ContainsGatewayURL bool   `json:"get_wasm_7_contains_gateway_url"`
				GetWASM7ContainsIntent     bool   `json:"get_wasm_7_contains_intent"`
			} `json:"registry_observation"`
			PortalDashboardWASMCount int    `json:"portal_dashboard_wasm_count"`
			Classification           string `json:"classification"`
			EvidenceArtifact         string `json:"evidence_artifact"`
		} `json:"corrected_registration_postflight"`
		Registration19Stage2Result struct {
			RegistrationID        int     `json:"registration_id"`
			AuthorAddress         string  `json:"author_address"`
			TransactionHash       string  `json:"transaction_hash"`
			ChainID               int     `json:"chain_id"`
			BlockNumber           int     `json:"block_number"`
			BlockTimestamp        string  `json:"block_timestamp"`
			TransactionStatus     int     `json:"transaction_status"`
			GasUsed               int     `json:"gas_used"`
			NativeValueWei        string  `json:"native_value_wei"`
			RegistryAddress       string  `json:"registry_address"`
			WASMURL               string  `json:"wasm_url"`
			WASMKeccak256RawBytes string  `json:"wasm_keccak256_raw_bytes"`
			Intent                string  `json:"intent"`
			IntentID              string  `json:"intent_id"`
			ActivationStatus      string  `json:"activation_status"`
			RejectionReason       string  `json:"rejection_reason"`
			EvalScore             float64 `json:"eval_score"`
			Stage1Passed          bool    `json:"stage_1_passed"`
			EvalDetails           struct {
				CandidateMargin         float64 `json:"candidate_margin"`
				ChampionMargin          float64 `json:"champion_margin"`
				CandidateWins           int     `json:"candidate_wins"`
				ChampionWins            int     `json:"champion_wins"`
				ComparableCases         int     `json:"comparable_cases"`
				WorstSelfMatch          float64 `json:"worst_self_match"`
				ScoreStddev             float64 `json:"score_stddev"`
				HistoricalRowsEvaluated int     `json:"historical_rows_evaluated"`
			} `json:"eval_details"`
		} `json:"registration_19_stage_2_result"`
		Registration41Stage2Result struct {
			SourceURL             string `json:"source_url"`
			EvidenceArtifact      string `json:"evidence_artifact"`
			RegistrationID        int    `json:"registration_id"`
			AuthorAddress         string `json:"author_address"`
			TransactionHash       string `json:"transaction_hash"`
			Chain                 string `json:"chain"`
			ChainID               int    `json:"chain_id"`
			BlockNumber           int    `json:"block_number"`
			BlockTimestamp        string `json:"block_timestamp"`
			TransactionStatus     int    `json:"transaction_status"`
			GasUsed               int    `json:"gas_used"`
			NativeValueWei        string `json:"native_value_wei"`
			ExplorerURL           string `json:"explorer_url"`
			OuterFrom             string `json:"outer_from"`
			OuterTo               string `json:"outer_to"`
			OuterSelector         string `json:"outer_selector"`
			OuterSignature        string `json:"outer_signature"`
			DelegatedWallet       string `json:"delegated_wallet"`
			RegistryAddress       string `json:"registry_address"`
			InnerTarget           string `json:"inner_target"`
			InnerValueWei         string `json:"inner_value_wei"`
			InnerSelector         string `json:"inner_selector"`
			InnerSignature        string `json:"inner_signature"`
			RegistrationSignature string `json:"registration_signature"`
			WASMURL               string `json:"wasm_url"`
			WASMByteSize          int    `json:"wasm_byte_size"`
			WASMSHA256            string `json:"wasm_sha256"`
			WASMKeccak256RawBytes string `json:"wasm_keccak256_raw_bytes"`
			HostedBytesVerified   bool   `json:"hosted_bytes_verified"`
			Intent                string `json:"intent"`
			IntentID              string `json:"intent_id"`
			RegistrationEvent     struct {
				Emitter        string `json:"emitter"`
				Event          string `json:"event"`
				RegistrationID int    `json:"registration_id"`
				Registrant     string `json:"registrant"`
				EntityType     int    `json:"entity_type"`
				IntentID       string `json:"intent_id"`
				ContentURL     string `json:"content_url"`
				ContentHash    string `json:"content_hash"`
			} `json:"registration_event"`
			ActivationStatus      string  `json:"activation_status"`
			RegisteredAtValidator string  `json:"registered_at_validator"`
			UpdatedAtValidator    string  `json:"updated_at_validator"`
			EvalScore             float64 `json:"eval_score"`
			Stage1Passed          bool    `json:"stage_1_passed"`
			Stage1Evidence        string  `json:"stage_1_evidence"`
			Stage2Promoted        bool    `json:"stage_2_promoted"`
			EvalDetails           struct {
				CandidateMargin         float64 `json:"candidate_margin"`
				ChampionMargin          float64 `json:"champion_margin"`
				CandidateWins           int     `json:"candidate_wins"`
				ChampionWins            int     `json:"champion_wins"`
				ComparableCases         int     `json:"comparable_cases"`
				WorstSelfMatch          float64 `json:"worst_self_match"`
				ScoreStddev             float64 `json:"score_stddev"`
				HistoricalRowsEvaluated int     `json:"historical_rows_evaluated"`
			} `json:"eval_details"`
			RejectionReason               string `json:"rejection_reason"`
			AggregateMarginNote           string `json:"aggregate_margin_note"`
			HiddenPairScoresDisclosed     bool   `json:"hidden_pair_scores_disclosed"`
			ManualAuthorizationConsumed   bool   `json:"manual_authorization_consumed"`
			FurtherRegistrationAuthorized bool   `json:"further_registration_authorized"`
		} `json:"registration_41_stage_2_result"`
	} `json:"verification"`
	RegistrationBoundary struct {
		ExternalGateOrder                                        []string `json:"external_gate_order"`
		CompletedExternalGates                                   []string `json:"completed_external_gates"`
		LiveValidatorRankOnlyRolloutConfirmed                    bool     `json:"live_validator_rank_only_rollout_confirmed"`
		CurrentExternalGate                                      string   `json:"current_external_gate"`
		ExternalGateOrderScope                                   string   `json:"external_gate_order_scope"`
		NextTransactionalGate                                    string   `json:"next_transactional_gate"`
		Uploaded                                                 bool     `json:"uploaded"`
		HostedBytesVerified                                      bool     `json:"hosted_bytes_verified"`
		HostedBytesVerificationSource                            *string  `json:"hosted_bytes_verification_source"`
		HostedGatewayURL                                         *string  `json:"hosted_gateway_url"`
		HostedIPFSCID                                            *string  `json:"hosted_ipfs_cid"`
		TransactionSubmitted                                     bool     `json:"transaction_submitted"`
		TransactionConfirmed                                     bool     `json:"transaction_confirmed"`
		TransactionEffectiveRegistrationObserved                 bool     `json:"transaction_effective_registration_observed"`
		CorrectedTransactionSubmitted                            bool     `json:"corrected_transaction_submitted"`
		CorrectedTransactionConfirmed                            bool     `json:"corrected_transaction_confirmed"`
		CorrectedTransactionEffectiveRegistrationObserved        bool     `json:"corrected_transaction_effective_registration_observed"`
		HistoricalFirstTransactionConfirmed                      bool     `json:"historical_first_transaction_confirmed"`
		HistoricalFirstTransactionEffectiveRegistrationObserved  bool     `json:"historical_first_transaction_effective_registration_observed"`
		HistoricalSecondTransactionConfirmed                     bool     `json:"historical_second_transaction_confirmed"`
		HistoricalSecondTransactionEffectiveRegistrationObserved bool     `json:"historical_second_transaction_effective_registration_observed"`
		RequiresSeparateAuthorization                            bool     `json:"requires_separate_authorization"`
		ExplicitUserAuthorizationReceived                        bool     `json:"explicit_user_authorization_received"`
		HistoricalRetryAuthorizationReceived                     bool     `json:"historical_retry_authorization_received"`
		HistoricalRetryAuthorizationConsumed                     bool     `json:"historical_retry_authorization_consumed"`
		CorrectedRegistrationAuthorizationConsumed               bool     `json:"corrected_registration_authorization_consumed"`
		FreshReregistrationAuthorizationReceived                 bool     `json:"fresh_reregistration_authorization_received"`
		Registration41AuthorizationConsumed                      bool     `json:"registration_41_authorization_consumed"`
		FurtherRegistrationAuthorized                            bool     `json:"further_registration_authorized"`
		RegistrationMode                                         string   `json:"registration_mode"`
		IntentBindingStatus                                      string   `json:"intent_binding_status"`
		ValidatorStage1Observed                                  bool     `json:"validator_stage_1_observed"`
		ValidatorStage1Passed                                    *bool    `json:"validator_stage_1_passed"`
		ReportedIntentThresholdResultObserved                    bool     `json:"reported_intent_threshold_result_observed"`
		ReportedIntentThresholdPassed                            *bool    `json:"reported_intent_threshold_passed"`
		ValidatorStage2Observed                                  bool     `json:"validator_stage_2_observed"`
		ValidatorStage2Promoted                                  *bool    `json:"validator_stage_2_promoted"`
	} `json:"registration_boundary"`
}

func loadReleaseEvidence(t *testing.T) releaseEvidence {
	t.Helper()
	bytes, err := os.ReadFile(filepath.Join(
		repoRoot(t), "scoring-modules", "oathcast-weather", "release-evidence.json",
	))
	if err != nil {
		t.Fatal(err)
	}
	var evidence releaseEvidence
	if err := json.Unmarshal(bytes, &evidence); err != nil {
		t.Fatal(err)
	}
	return evidence
}

func loadFixture(t *testing.T) fixtureFile {
	t.Helper()
	bytes, err := os.ReadFile(filepath.Join(repoRoot(t), "fixtures", "wasm_scoring_cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture fixtureFile
	if err := json.Unmarshal(bytes, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func (c fixtureCase) answer(t *testing.T) string {
	t.Helper()
	if c.MinerAnswer != nil {
		return *c.MinerAnswer
	}
	if c.MinerRepeat == nil || c.MinerRepeat.Count < 0 || c.MinerRepeat.Count > 100000 {
		t.Fatalf("invalid answer materialization for %s", c.CaseID)
	}
	return strings.Repeat(c.MinerRepeat.Value, c.MinerRepeat.Count)
}

func TestFixtureCorpus(t *testing.T) {
	scorer, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer scorer.close()
	fixture := loadFixture(t)
	type orderedScore struct {
		rank  int
		score float32
	}
	groups := map[string][]orderedScore{}

	// Floors, so a renamed fixture key cannot make this test pass vacuously by
	// running zero subtests. tests/test_wasm_scoring_fixture.py asserts the same
	// two invariants on the Python side.
	if len(fixture.Cases) < 27 {
		t.Fatalf("fixture corpus has %d cases, want at least 27", len(fixture.Cases))
	}

	for _, testCase := range fixture.Cases {
		t.Run(testCase.CaseID, func(t *testing.T) {
			score, err := scorer.score(testCase.Question, testCase.GroundTruth, testCase.answer(t))
			if err != nil {
				t.Fatal(err)
			}
			if testCase.Expected.Exact != nil && score != *testCase.Expected.Exact {
				t.Fatalf("score=%v, want exactly %v", score, *testCase.Expected.Exact)
			}
			if testCase.Expected.Min != nil && score+1e-6 < *testCase.Expected.Min {
				t.Fatalf("score=%v, want >= %v", score, *testCase.Expected.Min)
			}
			if testCase.Expected.Max != nil && score-1e-6 > *testCase.Expected.Max {
				t.Fatalf("score=%v, want <= %v", score, *testCase.Expected.Max)
			}
			if testCase.OrderingGroup != "" {
				groups[testCase.OrderingGroup] = append(
					groups[testCase.OrderingGroup], orderedScore{rank: testCase.QualityRank, score: score},
				)
			}
		})
	}
	if len(groups) == 0 {
		t.Fatal("fixture corpus produced no ordering groups; the transitivity check below would be vacuous")
	}
	for group, scores := range groups {
		for _, lower := range scores {
			for _, higher := range scores {
				if higher.rank > lower.rank && higher.score <= lower.score+1e-6 {
					t.Errorf(
						"ordering %s failed: rank %d score %.6f must beat rank %d score %.6f",
						group, higher.rank, higher.score, lower.rank, lower.score,
					)
				}
			}
		}
	}
}

func averageRanks(values []float64) []float64 {
	ranks := make([]float64, len(values))
	for index, value := range values {
		less := 0
		equal := 0
		for _, other := range values {
			if other < value {
				less++
			}
			if other == value {
				equal++
			}
		}
		ranks[index] = 1 + float64(less) + (float64(equal-1) / 2)
	}
	return ranks
}

func spearmanCorrelation(left, right []float64) float64 {
	if len(left) == 0 || len(left) != len(right) {
		return math.NaN()
	}
	leftRanks := averageRanks(left)
	rightRanks := averageRanks(right)
	var leftMean, rightMean float64
	for index := range leftRanks {
		leftMean += leftRanks[index]
		rightMean += rightRanks[index]
	}
	leftMean /= float64(len(leftRanks))
	rightMean /= float64(len(rightRanks))

	var covariance, leftVariance, rightVariance float64
	for index := range leftRanks {
		leftDelta := leftRanks[index] - leftMean
		rightDelta := rightRanks[index] - rightMean
		covariance += leftDelta * rightDelta
		leftVariance += leftDelta * leftDelta
		rightVariance += rightDelta * rightDelta
	}
	if leftVariance == 0 || rightVariance == 0 {
		return math.NaN()
	}
	return covariance / math.Sqrt(leftVariance*rightVariance)
}

func TestSyntheticOrdinalFactualPairs(t *testing.T) {
	scorer, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer scorer.close()
	fixture := loadFixture(t)
	evidence := loadReleaseEvidence(t)
	if len(fixture.FactualPairs) != evidence.Verification.SyntheticFactualPairCount {
		t.Fatalf(
			"factual pair corpus has %d pairs, release evidence pins %d",
			len(fixture.FactualPairs), evidence.Verification.SyntheticFactualPairCount,
		)
	}
	requiredCategories := []string{
		"acronym_case_binding",
		"acronym_abbreviation",
		"acronym_repetition",
		"ambiguous_correct_wrong_anchors",
		"benign_both",
		"benign_negation",
		"clause_aware_negation",
		"conjunctive_ambiguity",
		"contrast_relation_binding",
		"entity_name_recombination",
		"factual_wording_collision",
		"negative_factual_truth",
		"negated_correct_anchor",
		"numeric_non_regression",
		"partial_multiword_entity",
		"relation_entity_binding",
		"repeated_anchor_stuffing",
		"unrelated_shared_token",
		"verbose_truth_terse_answer",
		"weather_lexeme_collision",
		"weather_non_regression",
		"heldout_weather_lexeme_collision",
		"heldout_acronym_inference",
		"heldout_anchor_refutation",
		"heldout_directed_relation_binding",
		"heldout_directed_relation_ellipsis",
		"heldout_lowercase_context_binding",
		"heldout_name_alias_binding",
		"heldout_open_question_binding",
		"heldout_predicate_family_binding",
		"heldout_pre_anchor_refutation",
		"heldout_punctuation_binding",
		"heldout_source_attribution",
		"heldout_weather_context_omission",
	}
	seenCategories := make(map[string]bool, len(requiredCategories))
	seenPairIDs := make(map[string]bool, len(fixture.FactualPairs))
	seenSplits := make(map[string]bool, 2)
	var expectedQuality []float64
	var observedScores []float64
	weakestMargin := float32(2)
	weakestMarginPair := ""
	tightestBadCapSlack := float32(2)
	tightestBadCapPair := ""
	var tightestBadScore, tightestBadCap float32

	for _, pair := range fixture.FactualPairs {
		t.Run(pair.PairID, func(t *testing.T) {
			if seenPairIDs[pair.PairID] {
				t.Fatalf("duplicate factual pair ID %q", pair.PairID)
			}
			seenPairIDs[pair.PairID] = true
			if pair.Split != "development" && pair.Split != "secondary_synthetic" {
				t.Fatalf("unexpected split %q", pair.Split)
			}
			seenSplits[pair.Split] = true
			if pair.Category != "" {
				seenCategories[pair.Category] = true
			}
			if pair.MinimumMargin < 0.15 {
				t.Fatalf("minimum margin %.6f is below Telegraph's reported floor", pair.MinimumMargin)
			}
			if pair.MaximumBadScore != nil && (*pair.MaximumBadScore < 0 || *pair.MaximumBadScore > 1) {
				t.Fatalf("maximum bad score %.6f must be within [0, 1]", *pair.MaximumBadScore)
			}
			goodScore, err := scorer.score(pair.Question, pair.GroundTruth, pair.GoodAnswer)
			if err != nil {
				t.Fatal(err)
			}
			badScore, err := scorer.score(pair.Question, pair.GroundTruth, pair.BadAnswer)
			if err != nil {
				t.Fatal(err)
			}
			margin := goodScore - badScore
			if margin < weakestMargin {
				weakestMargin = margin
				weakestMarginPair = pair.PairID
			}
			if margin+1e-6 < pair.MinimumMargin {
				t.Fatalf(
					"good %.6f - bad %.6f = %.6f, want margin >= %.6f",
					goodScore, badScore, margin, pair.MinimumMargin,
				)
			}
			if pair.MaximumBadScore != nil {
				if badScore-1e-6 > *pair.MaximumBadScore {
					t.Fatalf("bad score %.6f exceeds cap %.6f", badScore, *pair.MaximumBadScore)
				}
				slack := *pair.MaximumBadScore - badScore
				if slack < tightestBadCapSlack {
					tightestBadCapSlack = slack
					tightestBadCapPair = pair.PairID
					tightestBadScore = badScore
					tightestBadCap = *pair.MaximumBadScore
				}
			}
			exactScore, err := scorer.score(pair.Question, pair.GroundTruth, pair.GroundTruth)
			if err != nil {
				t.Fatal(err)
			}
			if exactScore+1e-6 < goodScore {
				t.Fatalf("exact score %.6f must not be below good score %.6f", exactScore, goodScore)
			}
			expectedQuality = append(expectedQuality, 3, 2, 1)
			observedScores = append(
				observedScores,
				float64(exactScore),
				float64(goodScore),
				float64(badScore),
			)
		})
	}
	if !seenSplits["development"] || !seenSplits["secondary_synthetic"] || len(seenSplits) != 2 {
		t.Fatalf("factual pair splits = %v, want development and secondary_synthetic", seenSplits)
	}
	for _, category := range requiredCategories {
		if !seenCategories[category] {
			t.Errorf("factual pair corpus is missing required category %q", category)
		}
	}
	t.Logf("weakest synthetic factual margin: %.6f (%s)", weakestMargin, weakestMarginPair)
	if tightestBadCapPair != "" {
		t.Logf(
			"tightest synthetic factual bad-score cap: %.6f <= %.6f (%s)",
			tightestBadScore, tightestBadCap, tightestBadCapPair,
		)
	}

	correlation := spearmanCorrelation(expectedQuality, observedScores)
	t.Logf("synthetic ordinal Spearman correlation: %.6f", correlation)
	if math.IsNaN(correlation) || correlation < 0.60 {
		t.Fatalf("synthetic ordinal Spearman correlation %.6f, want >= 0.60", correlation)
	}
	if math.Abs(float64(weakestMargin)-evidence.Verification.SyntheticFactualMinimumMargin) > 1e-6 {
		t.Fatalf(
			"weakest synthetic margin %.6f does not match release evidence %.6f",
			weakestMargin, evidence.Verification.SyntheticFactualMinimumMargin,
		)
	}
	if math.Abs(correlation-evidence.Verification.SyntheticFactualOrdinalSpearman) > 1e-6 {
		t.Fatalf(
			"synthetic Spearman %.6f does not match release evidence %.6f",
			correlation, evidence.Verification.SyntheticFactualOrdinalSpearman,
		)
	}
}

func TestValidatorStructuralBehavior(t *testing.T) {
	scorer, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer scorer.close()

	question := "Will measurable precipitation occur in Lagos tomorrow?"
	groundTruth := "Yes. Measurable precipitation will occur in Lagos tomorrow."

	blankScore, err := scorer.score(question, groundTruth, " \t\r\n ")
	if err != nil {
		t.Fatal(err)
	}
	if blankScore != 0 {
		t.Fatalf("blank answer score=%v, want exactly 0", blankScore)
	}

	correctScore, err := scorer.score(question, groundTruth, groundTruth)
	if err != nil {
		t.Fatal(err)
	}
	unrelatedScore, err := scorer.score(question, groundTruth, "A compiler translates source code into another representation.")
	if err != nil {
		t.Fatal(err)
	}
	if correctScore <= unrelatedScore {
		t.Fatalf("correct score %.6f must beat unrelated score %.6f", correctScore, unrelatedScore)
	}

	unicodeAnswer := "\U0001f327\ufe0f \u660e\u65e5\u306f\u96e8\u3067\u3059\u3002 S\u00e3o Paulo ter\u00e1 chuva."
	unicodeScore, err := scorer.score("\u660e\u65e5\u306e\u5929\u6c17\u306f\uff1f", unicodeAnswer, unicodeAnswer)
	if err != nil {
		t.Fatal(err)
	}
	if unicodeScore != 1 {
		t.Fatalf("exact Unicode answer score=%v, want 1", unicodeScore)
	}

	longAnswer := strings.Repeat("\U0001f327\ufe0f\u5929\u6c17", 8*1024)
	if _, err := scorer.score(question, groundTruth, longAnswer); err != nil {
		t.Fatalf("long Unicode answer trapped: %v", err)
	}
}

func TestRepeatedCallsAreDeterministicAndResetAllocator(t *testing.T) {
	scorer, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer scorer.close()
	var first float32
	for index := 0; index < 5000; index++ {
		score, err := scorer.score("Will it rain?", "Yes. Rain will occur.", "Yes. Rain is likely.")
		if err != nil {
			t.Fatalf("call %d: %v", index, err)
		}
		if index == 0 {
			first = score
		} else if math.Float32bits(score) != math.Float32bits(first) {
			t.Fatalf("call %d returned %.9f, first was %.9f", index, score, first)
		}
	}
}

func TestMalformedAndUnallocatedInputsReturnZero(t *testing.T) {
	scorer, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer scorer.close()

	score, err := scorer.scoreBytes([]byte("question"), []byte("truth"), []byte{0xff, 0xfe})
	if err != nil {
		t.Fatal(err)
	}
	if score != 0 {
		t.Fatalf("invalid UTF-8 score=%v, want 0", score)
	}

	result, err := scorer.rank.Call(scorer.ctx, 1, 4, 1, 4, 1, 4)
	if err != nil {
		t.Fatal(err)
	}
	if api.DecodeF32(result[0]) != 0 {
		t.Fatal("unallocated pointers must score zero")
	}
}

func TestOutOfBoundsLengthReturnsZero(t *testing.T) {
	scorer, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer scorer.close()

	result, err := scorer.rank.Call(scorer.ctx, 0, uint64(maxQuestionBytes+1), 0, 0, 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if api.DecodeF32(result[0]) != 0 {
		t.Fatal("out-of-bounds length must score zero")
	}
}

func TestOversizedAllocationTrapsWithoutCorruptingNewInstance(t *testing.T) {
	scorer, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	_, err = scorer.alloc.Call(scorer.ctx, 2*1024*1024)
	if err == nil {
		t.Fatal("oversized allocation should trap")
	}
	scorer.close()

	fresh, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer fresh.close()
	if score, err := fresh.score("q", "rain", "rain"); err != nil || score != 1 {
		t.Fatalf("fresh instance score=%v err=%v", score, err)
	}
}

func TestInputByteBoundaries(t *testing.T) {
	tests := []struct {
		name        string
		question    string
		truth       string
		answer      string
		wantAtCap   float32
		wantOverCap float32
	}{
		{
			name:        "miner answer",
			question:    "q",
			truth:       strings.Repeat("a", maxMinerAnswerBytes),
			answer:      strings.Repeat("a", maxMinerAnswerBytes),
			wantAtCap:   1,
			wantOverCap: 0,
		},
		{
			name:        "question",
			question:    strings.Repeat("q", maxQuestionBytes),
			truth:       "rain",
			answer:      "rain",
			wantAtCap:   1,
			wantOverCap: 0,
		},
		{
			name:        "ground truth",
			question:    "q",
			truth:       "rain" + strings.Repeat(" ", maxGroundTruthBytes-len("rain")),
			answer:      "rain",
			wantAtCap:   1,
			wantOverCap: 0,
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			scorer, err := openScorer(wasmPath(t))
			if err != nil {
				t.Fatal(err)
			}
			defer scorer.close()

			atCap, err := scorer.score(testCase.question, testCase.truth, testCase.answer)
			if err != nil {
				t.Fatalf("at-cap call: %v", err)
			}
			if atCap != testCase.wantAtCap {
				t.Fatalf("at-cap score=%v, want %v", atCap, testCase.wantAtCap)
			}

			question, truth, answer := testCase.question, testCase.truth, testCase.answer
			switch testCase.name {
			case "miner answer":
				answer += "a"
			case "question":
				question += "q"
			case "ground truth":
				truth += " "
			}
			overCap, err := scorer.score(question, truth, answer)
			if err != nil {
				t.Fatalf("over-cap call: %v", err)
			}
			if overCap != testCase.wantOverCap {
				t.Fatalf("over-cap score=%v, want %v", overCap, testCase.wantOverCap)
			}
		})
	}
}

func TestDeallocRequiresExactLivePair(t *testing.T) {
	t.Run("exact pair invalidates allocation", func(t *testing.T) {
		scorer, err := openScorer(wasmPath(t))
		if err != nil {
			t.Fatal(err)
		}
		defer scorer.close()
		qPtr, qLen, err := scorer.writeBytes([]byte("q"))
		if err != nil {
			t.Fatal(err)
		}
		gtPtr, gtLen, err := scorer.writeBytes([]byte("rain"))
		if err != nil {
			t.Fatal(err)
		}
		answerPtr, answerLen, err := scorer.writeBytes([]byte("rain"))
		if err != nil {
			t.Fatal(err)
		}
		if _, err := scorer.dealloc.Call(scorer.ctx, uint64(answerPtr), uint64(answerLen)); err != nil {
			t.Fatal(err)
		}
		result, err := scorer.rank.Call(
			scorer.ctx,
			uint64(qPtr), uint64(qLen),
			uint64(gtPtr), uint64(gtLen),
			uint64(answerPtr), uint64(answerLen),
		)
		if err != nil {
			t.Fatal(err)
		}
		if score := api.DecodeF32(result[0]); score != 0 {
			t.Fatalf("deallocated answer score=%v, want 0", score)
		}
	})

	t.Run("mismatched pair is ignored", func(t *testing.T) {
		scorer, err := openScorer(wasmPath(t))
		if err != nil {
			t.Fatal(err)
		}
		defer scorer.close()
		qPtr, qLen, err := scorer.writeBytes([]byte("q"))
		if err != nil {
			t.Fatal(err)
		}
		gtPtr, gtLen, err := scorer.writeBytes([]byte("rain"))
		if err != nil {
			t.Fatal(err)
		}
		answerPtr, answerLen, err := scorer.writeBytes([]byte("rain"))
		if err != nil {
			t.Fatal(err)
		}
		if _, err := scorer.dealloc.Call(scorer.ctx, uint64(answerPtr), uint64(answerLen-1)); err != nil {
			t.Fatal(err)
		}
		result, err := scorer.rank.Call(
			scorer.ctx,
			uint64(qPtr), uint64(qLen),
			uint64(gtPtr), uint64(gtLen),
			uint64(answerPtr), uint64(answerLen),
		)
		if err != nil {
			t.Fatal(err)
		}
		if score := api.DecodeF32(result[0]); score != 1 {
			t.Fatalf("mismatched dealloc score=%v, want 1", score)
		}
	})
}

func TestJSONEscapingIsValidatedAndScored(t *testing.T) {
	scorer, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer scorer.close()

	t.Run("valid escaped content", func(t *testing.T) {
		score, err := scorer.score(
			"q",
			`Rain "likely".`,
			`{"content":"Rain \"likely\".","metadata":"line one\nline two","probability":0.65}`,
		)
		if err != nil {
			t.Fatal(err)
		}
		if score != 1 {
			t.Fatalf("escaped JSON content score=%v, want 1", score)
		}
	})

	t.Run("decoded punctuation remains meaningful", func(t *testing.T) {
		score, err := scorer.score(
			"q",
			"Rain likely.",
			`{"content":"Rain \"likely\".","metadata":"line one\nline two","probability":0.65}`,
		)
		if err != nil {
			t.Fatal(err)
		}
		if score < 0.8 || score >= 1 {
			t.Fatalf("punctuation-different JSON content score=%v, want [0.8, 1)", score)
		}
	})

	t.Run("invalid escape is rejected", func(t *testing.T) {
		score, err := scorer.score("q", "rain", `{"content":"rain\q"}`)
		if err != nil {
			t.Fatal(err)
		}
		if score != 0 {
			t.Fatalf("invalid JSON escape score=%v, want 0", score)
		}
	})
}
