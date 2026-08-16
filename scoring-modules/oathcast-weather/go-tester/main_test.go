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
	if evidence.SchemaVersion != "oathcast_weather_wasm_release_evidence_v4" {
		t.Fatalf("unexpected release evidence schema %q", evidence.SchemaVersion)
	}
	if evidence.Status != "rank_only_current_registry_id_7_dashboard_unindexed_validator_unobserved" {
		t.Fatalf("unexpected release evidence status %q", evidence.Status)
	}
	if !evidence.Artifact.RegistrationCandidate {
		t.Fatal("rank-only artifact must be marked as a registration candidate")
	}
	if evidence.CurrentArtifactABI.FunctionExportCount != 3 {
		t.Fatalf("release evidence pins %d function exports, want 3", evidence.CurrentArtifactABI.FunctionExportCount)
	}
	if evidence.CurrentArtifactABI.BreakdownAnswerExported {
		t.Fatal("release evidence must not claim a breakdown_answer export")
	}
	wantGateOrder := []string{
		"dashboard_or_validator_indexing_observation",
		"validator_stage_1_and_reported_intent_threshold_result",
		"validator_stage_2_promotion_result",
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
		"exact_candidate_and_weather_forecast_binding_verified",
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
	if evidence.RegistrationBoundary.CurrentExternalGate != "dashboard_or_validator_indexing_observation" {
		t.Fatalf(
			"unexpected current external gate %q",
			evidence.RegistrationBoundary.CurrentExternalGate,
		)
	}
	if !evidence.RegistrationBoundary.Uploaded || !evidence.RegistrationBoundary.HostedBytesVerified {
		t.Fatal("release evidence must record the portal upload and hosted-byte verification")
	}
	if !evidence.RegistrationBoundary.TransactionSubmitted || !evidence.RegistrationBoundary.TransactionConfirmed {
		t.Fatal("release evidence must record the confirmed portal transaction")
	}
	if !evidence.RegistrationBoundary.TransactionEffectiveRegistrationObserved ||
		!evidence.RegistrationBoundary.CorrectedTransactionSubmitted ||
		!evidence.RegistrationBoundary.CorrectedTransactionConfirmed ||
		!evidence.RegistrationBoundary.CorrectedTransactionEffectiveRegistrationObserved {
		t.Fatal("release evidence must record the effective corrected current-registry entry")
	}
	if !evidence.RegistrationBoundary.ExplicitUserAuthorizationReceived {
		t.Fatal("release evidence must retain the user's provisional registration authorization")
	}
	if !evidence.RegistrationBoundary.HistoricalRetryAuthorizationReceived ||
		!evidence.RegistrationBoundary.HistoricalRetryAuthorizationConsumed ||
		!evidence.RegistrationBoundary.CorrectedRegistrationAuthorizationConsumed ||
		evidence.RegistrationBoundary.FreshReregistrationAuthorizationReceived ||
		evidence.RegistrationBoundary.FurtherRegistrationAuthorized {
		t.Fatal("release evidence must record consumed historical authorizations and block a fresh attempt")
	}
	if evidence.RegistrationBoundary.RegistrationMode != "current_registry_id_7_registered_dashboard_unindexed_validator_unobserved" {
		t.Fatalf("unexpected registration mode %q", evidence.RegistrationBoundary.RegistrationMode)
	}
	if evidence.RegistrationBoundary.IntentBindingStatus != "current_registry_exact_candidate_and_weather_forecast_binding_observed" {
		t.Fatalf("unexpected Intent binding status %q", evidence.RegistrationBoundary.IntentBindingStatus)
	}
	if evidence.RegistrationBoundary.ValidatorStage1Observed ||
		evidence.RegistrationBoundary.ReportedIntentThresholdResultObserved ||
		evidence.RegistrationBoundary.ValidatorStage2Observed ||
		evidence.RegistrationBoundary.ValidatorStage1Passed != nil ||
		evidence.RegistrationBoundary.ReportedIntentThresholdPassed != nil ||
		evidence.RegistrationBoundary.ValidatorStage2Promoted != nil {
		t.Fatal("release evidence must preserve unobserved validator and threshold results")
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
	if postflight.InnerWASMURL != evidence.RegistrationBoundary.HostedGatewayURL {
		t.Fatalf("transaction WASM URL %q does not match hosted gateway %q", postflight.InnerWASMURL, evidence.RegistrationBoundary.HostedGatewayURL)
	}
	if postflight.InnerWASMHash != evidence.Artifact.Keccak256RawBytes {
		t.Fatalf("transaction WASM hash %q does not match artifact %q", postflight.InnerWASMHash, evidence.Artifact.Keccak256RawBytes)
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
	if second.InnerWASMHash != evidence.Artifact.Keccak256RawBytes ||
		second.InnerWASMURL != evidence.RegistrationBoundary.HostedGatewayURL || len(second.InnerWhitelistedURLs) != 0 {
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
		preflight.WASMHash != evidence.Artifact.Keccak256RawBytes ||
		preflight.WASMURL != evidence.RegistrationBoundary.HostedGatewayURL ||
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
		corrected.InnerWASMHash != evidence.Artifact.Keccak256RawBytes ||
		corrected.InnerWASMURL != evidence.RegistrationBoundary.HostedGatewayURL ||
		corrected.InnerIntent != "WEATHER_FORECAST" {
		t.Fatalf("unexpected corrected nested registration call: %+v", corrected)
	}
	if corrected.EventEmitter != corrected.InnerTarget || corrected.EventRegistrationID != 7 ||
		corrected.EventRegistrant != corrected.DelegatedWallet || corrected.EventEntityType != 2 ||
		corrected.EventIntentID != "0x9eefcfc9ee9243dea613f4a518d6a4602dfacbd6ad1efe17f9239824a69a034e" ||
		corrected.EventContentHash != evidence.Artifact.Keccak256RawBytes ||
		corrected.EventContentURL != evidence.RegistrationBoundary.HostedGatewayURL {
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

type fixtureFile struct {
	Cases []fixtureCase `json:"cases"`
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
		} `json:"telegraph_team_clarifications"`
	} `json:"authority_snapshots"`
	Verification struct {
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
	} `json:"verification"`
	RegistrationBoundary struct {
		ExternalGateOrder                                 []string `json:"external_gate_order"`
		CompletedExternalGates                            []string `json:"completed_external_gates"`
		LiveValidatorRankOnlyRolloutConfirmed             bool     `json:"live_validator_rank_only_rollout_confirmed"`
		CurrentExternalGate                               string   `json:"current_external_gate"`
		Uploaded                                          bool     `json:"uploaded"`
		HostedBytesVerified                               bool     `json:"hosted_bytes_verified"`
		TransactionSubmitted                              bool     `json:"transaction_submitted"`
		TransactionConfirmed                              bool     `json:"transaction_confirmed"`
		TransactionEffectiveRegistrationObserved          bool     `json:"transaction_effective_registration_observed"`
		CorrectedTransactionSubmitted                     bool     `json:"corrected_transaction_submitted"`
		CorrectedTransactionConfirmed                     bool     `json:"corrected_transaction_confirmed"`
		CorrectedTransactionEffectiveRegistrationObserved bool     `json:"corrected_transaction_effective_registration_observed"`
		ExplicitUserAuthorizationReceived                 bool     `json:"explicit_user_authorization_received"`
		HistoricalRetryAuthorizationReceived              bool     `json:"historical_retry_authorization_received"`
		HistoricalRetryAuthorizationConsumed              bool     `json:"historical_retry_authorization_consumed"`
		CorrectedRegistrationAuthorizationConsumed        bool     `json:"corrected_registration_authorization_consumed"`
		FreshReregistrationAuthorizationReceived          bool     `json:"fresh_reregistration_authorization_received"`
		FurtherRegistrationAuthorized                     bool     `json:"further_registration_authorized"`
		RegistrationMode                                  string   `json:"registration_mode"`
		IntentBindingStatus                               string   `json:"intent_binding_status"`
		ValidatorStage1Observed                           bool     `json:"validator_stage_1_observed"`
		ValidatorStage1Passed                             *bool    `json:"validator_stage_1_passed"`
		ReportedIntentThresholdResultObserved             bool     `json:"reported_intent_threshold_result_observed"`
		ReportedIntentThresholdPassed                     *bool    `json:"reported_intent_threshold_passed"`
		ValidatorStage2Observed                           bool     `json:"validator_stage_2_observed"`
		ValidatorStage2Promoted                           *bool    `json:"validator_stage_2_promoted"`
		HostedGatewayURL                                  string   `json:"hosted_gateway_url"`
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
			"Rain likely.",
			`{"content":"Rain \"likely\".","metadata":"line one\nline two","probability":0.65}`,
		)
		if err != nil {
			t.Fatal(err)
		}
		if score != 1 {
			t.Fatalf("escaped JSON content score=%v, want 1", score)
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
