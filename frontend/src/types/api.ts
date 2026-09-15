export type ServiceState = 'healthy' | 'degraded' | 'unavailable';
export type QualityState = 'HEALTHY' | 'WARNING' | 'FAILED';

export interface HealthResponse {
  status: ServiceState;
  api: ServiceState;
  database: ServiceState;
  version: string;
}

export interface Security {
  id: string;
  exchange: string;
  symbol: string;
  trading_symbol: string;
  company_name: string;
  isin: string | null;
  security_type: string;
  sector: string | null;
  industry: string | null;
  currency: string;
  listing_date: string | null;
  delisting_date: string | null;
  is_active: boolean;
}

export interface SecuritiesPage {
  items: Security[];
  total: number;
  page: number;
  page_size: number;
}

export interface PriceRow {
  trading_date: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: number;
  traded_value: string | null;
  source: string;
  view: 'raw' | 'adjusted';
  adjustment_factor: string;
}

export interface PriceSeries {
  symbol: string;
  view: 'raw' | 'adjusted';
  items: PriceRow[];
}

export interface QualityCheck {
  name: string;
  status: QualityState;
  message: string;
  issue_count: number;
}

export interface QualityResponse {
  status: QualityState;
  checked_at: string;
  latest_data_date: string | null;
  last_successful_ingestion: string | null;
  checks: QualityCheck[];
}

export type DatasetMode = 'DEMO' | 'OFFICIAL_NSE' | 'MIXED' | 'UNKNOWN';

export interface DataSourceDefinition {
  artifact_type: string;
  provider: string;
  owner: string;
  landing_page: string;
  artifact_kind: string;
  parser_code: string;
  parser_version: string;
  automation_suitability: string;
  point_in_time_limit: string;
  latest_artifact_status: string | null;
  latest_source_date: string | null;
  latest_imported_at: string | null;
}

export interface DataSourcesResponse {
  mode: DatasetMode;
  sources: DataSourceDefinition[];
  redistribution_notice: string;
}

export interface SourceArtifactSummary {
  id: string;
  provider: string;
  artifact_type: string;
  source_date: string;
  imported_at: string;
  sha256: string;
  parser_code: string;
  parser_version: string;
  parse_status: string;
  row_count: number;
  accepted_row_count: number;
  rejected_row_count: number;
  warning_count: number;
}

export interface IngestionIssueSummary {
  severity: 'ERROR' | 'WARNING' | 'INFO';
  code: string;
  message: string;
  row_key: string | null;
  created_at: string;
}

export interface IndexCoverage {
  symbol: string;
  label: string;
  current_snapshot_present: boolean;
  snapshot_as_of: string | null;
  member_count: number;
  coverage_start: string | null;
  coverage_end: string | null;
  coverage_kind: 'NONE' | 'CURRENT_SNAPSHOT_ONLY' | 'BOUNDED_SNAPSHOT_SEQUENCE';
  warning: string | null;
}

export interface DataCoverageResponse {
  mode: DatasetMode;
  provider: string | null;
  last_successful_ingestion: string | null;
  earliest_official_price_session: string | null;
  latest_official_price_session: string | null;
  official_security_count: number;
  official_daily_price_count: number;
  demo_security_count: number;
  demo_daily_price_count: number;
  source_artifact_count: number;
  corporate_actions_promoted: number;
  corporate_actions_quarantined: number;
  missing_official_sessions: number;
  index_coverage: IndexCoverage[];
  latest_artifacts: SourceArtifactSummary[];
  latest_issues: IngestionIssueSummary[];
  warnings: string[];
}

export interface FeatureDefinition {
  code: string;
  version: string;
  name: string;
  category: string;
  description: string;
  formula: string;
  required_fields: string[];
  lookback_sessions: number;
  minimum_observations: number;
  output_type: 'DECIMAL' | 'BOOLEAN';
  unit: string;
  parameters: Record<string, number | string>;
  adjustment_support: string;
  availability_semantics: string;
}

export interface FeatureCatalog {
  definitions: FeatureDefinition[];
  count: number;
}

export interface FeatureSet {
  code: string;
  version: string;
  name: string;
  description: string;
  feature_codes: string[];
  default_adjustment_policy: 'ADJUSTED';
  is_active: boolean;
}

export interface FeatureSetCatalog {
  feature_sets: FeatureSet[];
}

export interface DatasetContext {
  dataset_code: string;
  dataset_version: string;
  provider: string;
  earliest_observation: string | null;
  latest_observation: string | null;
  fingerprint: string;
}

export type FeatureValue = string | boolean | null;

export interface FeatureObservation {
  observation_date: string;
  available_at: string;
  values: Record<string, FeatureValue>;
  unavailable: Record<string, string>;
}

export interface SecurityFeatureSeries {
  security_id: string;
  symbol: string;
  exchange: string;
  feature_set: FeatureSet;
  feature_versions: Record<string, string>;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  dataset: DatasetContext;
  requested_start: string | null;
  requested_end: string | null;
  as_of: string;
  computed_at: string;
  quality_warnings: string[];
  items: FeatureObservation[];
}

export type ScannerOperator = '>' | '>=' | '<' | '<=' | '=' | 'between';

export interface ScannerFilter {
  feature: string;
  operator: ScannerOperator;
  value: string | boolean;
  upper_value?: string | null;
}

export interface ScannerFeatureMetadata {
  code: string;
  display_name: string;
  family: string;
  version: string;
  value_type: 'DECIMAL' | 'BOOLEAN';
  unit: string;
  minimum_observations: number;
  supported_operators: ScannerOperator[];
  scanner_filtering_supported: boolean;
}

export interface ScannerOperatorMetadata {
  code: ScannerOperator;
  label: string;
  requires_upper_value: boolean;
}

export interface ScannerUniverse {
  id: string;
  symbol: string;
  name: string;
  provider: string;
  exchange: string;
}

export interface ScannerMetadata {
  feature_set: FeatureSet;
  features: ScannerFeatureMetadata[];
  operators: ScannerOperatorMetadata[];
  adjustment_policies: ('RAW' | 'ADJUSTED')[];
  universes: ScannerUniverse[];
  latest_observation_date: string | null;
}

export interface MarketScanRequest {
  universe: string;
  observation_date: string;
  as_of: string;
  feature_set: string;
  feature_set_version: string;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  logic: 'AND';
  filters: ScannerFilter[];
}

export interface ScannerResult {
  security_id: string;
  symbol: string;
  company_name: string;
  exchange: string;
  observation_date: string;
  as_of: string;
  available_at: string;
  matched_values: Record<string, Exclude<FeatureValue, null>>;
  feature_versions: Record<string, string>;
  feature_set_code: string;
  feature_set_version: string;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  dataset: DatasetContext;
  input_fingerprint: string;
  quality_warnings: string[];
}

export interface ScannerTimings {
  universe_resolution_ms: number;
  feature_computation_ms: number;
  predicate_evaluation_ms: number;
  response_build_ms: number;
  total_service_ms: number;
}

export interface MarketScanResponse {
  universe: ScannerUniverse;
  observation_date: string;
  as_of: string;
  executed_at: string;
  feature_set: FeatureSet;
  feature_versions: Record<string, string>;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  logic: 'AND';
  filters: ScannerFilter[];
  universe_member_count: number;
  evaluated_security_count: number;
  unavailable_security_count: number;
  matched_count: number;
  result_order: 'SYMBOL_ASC';
  scan_fingerprint: string;
  timings: ScannerTimings;
  results: ScannerResult[];
}

export type StrategyScalar = string | boolean;

export interface StrategyParameterMetadata {
  code: string;
  display_name: string;
  description: string;
  value_type: 'DECIMAL' | 'BOOLEAN';
  default_value: StrategyScalar;
  minimum: string | null;
  maximum: string | null;
}

export interface StrategyRuleMetadata {
  feature_code: string;
  feature_version: string;
  operator: '>' | '>=' | '<' | '<=' | '=';
  parameter_code: string;
  default_expected_value: StrategyScalar;
  unit: string;
}

export interface StrategyMetadata {
  strategy_code: string;
  strategy_version: string;
  display_name: string;
  description: string;
  direction: 'LONG_ONLY';
  research_horizon: string;
  required_feature_set: string;
  required_feature_set_version: string;
  required_feature_codes: string[];
  default_adjustment_policy: 'ADJUSTED';
  evaluation_timing_policy: 'EOD_AFTER_CLOSE';
  parameters: StrategyParameterMetadata[];
  rules: StrategyRuleMetadata[];
  minimum_warmup_observations: number;
  status: 'ACTIVE';
  default_strategy_fingerprint: string;
}

export interface StrategyCatalog {
  strategies: StrategyMetadata[];
  universes: ScannerUniverse[];
  adjustment_policies: ('RAW' | 'ADJUSTED')[];
  latest_observation_date: string | null;
  research_disclaimer: string;
}

export interface StrategyEvaluationRequest {
  strategy_code: string;
  strategy_version: string;
  universe: string;
  observation_date: string;
  as_of: string;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  parameter_overrides: Record<string, StrategyScalar>;
}

export interface StrategyConditionResult {
  feature_code: string;
  feature_version: string;
  operator: '>' | '>=' | '<' | '<=' | '=';
  expected_value: StrategyScalar;
  actual_value: FeatureValue;
  passed: boolean;
  unit: string;
}

export interface StrategySecurityResult {
  security_id: string;
  symbol: string;
  company_name: string;
  exchange: string;
  observation_date: string;
  as_of: string;
  available_at: string | null;
  strategy_code: string;
  strategy_version: string;
  direction: 'LONG_ONLY';
  matched: boolean;
  required_feature_values: Record<string, FeatureValue>;
  conditions: StrategyConditionResult[];
  passed_condition_count: number;
  total_condition_count: number;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  feature_set_code: string;
  feature_set_version: string;
  feature_versions: Record<string, string>;
  dataset: DatasetContext;
  input_fingerprint: string;
  strategy_fingerprint: string;
  result_fingerprint: string;
  explanation: string;
  warnings: string[];
}

export interface StrategyEvaluationResponse {
  universe: ScannerUniverse;
  observation_date: string;
  as_of: string;
  executed_at: string;
  strategy: StrategyMetadata;
  effective_parameters: Record<string, StrategyScalar>;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  universe_member_count: number;
  evaluated_security_count: number;
  unavailable_security_count: number;
  matched_count: number;
  result_order: 'SYMBOL_ASC';
  evaluation_fingerprint: string;
  warnings: string[];
  timings: {
    universe_resolution_ms: number;
    feature_computation_ms: number;
    condition_evaluation_ms: number;
    response_build_ms: number;
    total_service_ms: number;
  };
  results: StrategySecurityResult[];
}

export type CompositionStatus = 'MATCHED' | 'NOT_MATCHED' | 'INSUFFICIENT_FEATURE_HISTORY';
export type ReplayStatus = 'INITIAL' | 'REPRODUCED' | 'DATASET_DRIFT_DETECTED' | 'ENGINE_OR_RESULT_DRIFT_DETECTED';

export interface CompositionComponentRequest {
  strategy_code: string;
  strategy_version: string;
  parameter_overrides: Record<string, StrategyScalar>;
}

export interface CompositionEvaluationRequest {
  policy_code: string;
  policy_version: string;
  universe: string;
  observation_date: string;
  as_of: string;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  required_match_count: number;
  components: CompositionComponentRequest[];
}

export interface CompositionPolicyMetadata {
  policy_code: string;
  policy_version: string;
  display_name: string;
  description: string;
  minimum_components: number;
  maximum_components: number;
  aggregation_rule: 'AT_LEAST_N_MATCHED';
  insufficient_history_rule: 'COULD_CHANGE_THRESHOLD';
  component_order_policy: 'CANONICAL_STRATEGY_PARAMETERS';
  policy_fingerprint: string;
}

export interface CompositionMetadata {
  policies: CompositionPolicyMetadata[];
  strategies: StrategyMetadata[];
  universes: ScannerUniverse[];
  adjustment_policies: ('RAW' | 'ADJUSTED')[];
  latest_observation_date: string | null;
  research_disclaimer: string;
}

export interface CompositionComponentConfiguration {
  strategy: StrategyMetadata;
  effective_parameters: Record<string, StrategyScalar>;
  strategy_fingerprint: string;
}

export interface CompositionComponentResult {
  strategy_code: string;
  strategy_version: string;
  status: CompositionStatus;
  effective_parameters: Record<string, StrategyScalar>;
  required_feature_values: Record<string, FeatureValue>;
  conditions: StrategyConditionResult[];
  passed_condition_count: number;
  total_condition_count: number;
  strategy_fingerprint: string;
  result_fingerprint: string;
  warnings: string[];
}

export interface CompositionSecurityResult {
  security_id: string;
  symbol: string;
  company_name: string;
  exchange: string;
  observation_date: string;
  as_of: string;
  available_at: string | null;
  status: CompositionStatus;
  matched_strategy_count: number;
  insufficient_strategy_count: number;
  required_match_count: number;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  input_fingerprint: string;
  composition_result_fingerprint: string;
  component_results: CompositionComponentResult[];
  warnings: string[];
}

export interface CompositionEvaluationResponse {
  normalized_request: CompositionEvaluationRequest;
  policy: CompositionPolicyMetadata;
  components: CompositionComponentConfiguration[];
  universe: ScannerUniverse;
  observation_date: string;
  as_of: string;
  executed_at: string;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  union_feature_codes: string[];
  universe_member_count: number;
  evaluated_security_count: number;
  matched_count: number;
  not_matched_count: number;
  insufficient_history_count: number;
  result_order: 'SYMBOL_ASC';
  component_order: 'STRATEGY_CODE_VERSION_PARAMETERS_ASC';
  composition_config_fingerprint: string;
  composition_dataset_fingerprint: string;
  composition_run_fingerprint: string;
  engine_provenance: Record<string, unknown>;
  warnings: string[];
  timings: {
    universe_resolution_ms: number;
    feature_computation_ms: number;
    component_evaluation_ms: number;
    composition_ms: number;
    response_build_ms: number;
    total_service_ms: number;
  };
  results: CompositionSecurityResult[];
}

export interface ExperimentRunSummary {
  total_members: number;
  matched: number;
  not_matched: number;
  insufficient_history: number;
}

export interface ExperimentMemberOutcome {
  symbol: string;
  status: CompositionStatus;
  matched_strategy_count: number;
  insufficient_strategy_count: number;
  required_match_count: number;
  composition_result_fingerprint: string;
  components: Array<Record<string, string>>;
}

export interface ExperimentRun {
  id: string;
  experiment_id: string;
  executed_at: string;
  replay_status: ReplayStatus;
  reference_run_id: string | null;
  normalized_request: CompositionEvaluationRequest;
  composition_config_fingerprint: string;
  dataset_fingerprint: string;
  run_fingerprint: string;
  engine_provenance: Record<string, unknown>;
  summary: ExperimentRunSummary;
  member_outcomes: ExperimentMemberOutcome[];
  warnings: string[];
}

export interface ExperimentDefinition {
  id: string;
  name: string;
  description: string | null;
  policy_code: string;
  policy_version: string;
  normalized_request: CompositionEvaluationRequest;
  config_fingerprint: string;
  created_at: string;
  latest_run: ExperimentRun | null;
}

export interface ExperimentListItem {
  id: string;
  name: string;
  description: string | null;
  policy_code: string;
  policy_version: string;
  strategy_count: number;
  required_match_count: number;
  universe: string;
  observation_date: string;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  config_fingerprint: string;
  created_at: string;
  latest_replay_status: ReplayStatus | null;
  latest_run_at: string | null;
  latest_run_fingerprint: string | null;
}

export interface ExperimentListResponse {
  items: ExperimentListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface ExperimentRunListResponse {
  items: ExperimentRun[];
  total: number;
  page: number;
  page_size: number;
}

export interface ExperimentCreateRequest {
  name: string;
  description: string | null;
  composition: CompositionEvaluationRequest;
}

export interface ExperimentCreateResponse {
  experiment: ExperimentDefinition;
  initial_evaluation: CompositionEvaluationResponse;
}

export interface ExperimentReplayResponse {
  experiment: ExperimentDefinition;
  run: ExperimentRun;
  evaluation: CompositionEvaluationResponse;
}

export interface BacktestProfileMetadata {
  profile_code: string;
  profile_version: string;
  display_name: string;
  compatible_strategies: string[];
  default_holding_sessions: Record<string, number>;
  entry_timing: string;
  exit_timing: string;
  default_slippage_bps: string;
  default_trade_notional_inr: string;
  quantity_policy: string;
  stop_policy: string;
  target_policy: string;
  overlap_policy: string;
  end_policy: string;
  missing_entry_policy: string;
  missing_exit_policy: string;
  default_max_exit_delay_sessions: number;
  cost_model_code: string;
  cost_model_version: string;
  profile_fingerprint: string;
}

export interface BacktestCostModelMetadata {
  code: string;
  version: string;
  display_name: string;
  stt_buy_rate: string;
  stt_sell_rate: string;
  exchange_transaction_rate: string;
  sebi_turnover_rate: string;
  gst_rate: string;
  stamp_duty_buy_rate: string;
  gst_taxable_components: string[];
  monetary_rounding: string;
  stt_rounding: string;
  default_brokerage_per_order_inr: string;
  default_brokerage_rate: string;
  default_dp_charge_per_scrip_sell_day_inr: string;
  effective_brokerage_per_order_inr: string;
  effective_brokerage_rate: string;
  effective_dp_charge_per_scrip_sell_day_inr: string;
  cost_model_fingerprint: string;
  broker_specific_costs_excluded_by_default: boolean;
}

export interface BacktestMetadata {
  profiles: BacktestProfileMetadata[];
  cost_models: BacktestCostModelMetadata[];
  strategies: StrategyMetadata[];
  universes: ScannerUniverse[];
  adjustment_policies: ('RAW' | 'ADJUSTED')[];
  parameter_bounds: Record<string, { minimum: string | number; maximum: string | number; default: string | number | null }>;
  overlap_policies: string[];
  end_policies: string[];
  sample_size_warning_rules: Record<string, number>;
  hard_request_limits: Record<string, number>;
  latest_observation_date: string | null;
  research_disclaimer: string;
}

export interface BacktestRunRequest {
  strategy_code: string;
  strategy_version: string;
  parameter_overrides: Record<string, StrategyScalar>;
  universe: string;
  start_date: string;
  end_date: string;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  profile_code: string;
  profile_version: string;
  holding_sessions: number;
  trade_notional_inr: string;
  slippage_bps: string;
  stop_loss_pct: string | null;
  profit_target_pct: string | null;
  max_exit_delay_sessions: number;
  cost_model_code: string;
  cost_model_version: string;
  brokerage_per_order_inr: string;
  brokerage_rate: string;
  dp_charge_per_scrip_sell_day_inr: string;
  out_of_sample_start_date: string | null;
  trade_detail_limit: number;
}

export interface BacktestCostBreakdown {
  turnover: string;
  brokerage: string;
  stt: string;
  exchange_transaction_charge: string;
  sebi_charge: string;
  gst: string;
  stamp_duty: string;
  dp_charge: string;
  total_charges: string;
}

export interface BacktestTrade {
  trade_id: string;
  security_id: string;
  symbol: string;
  company_name: string;
  strategy_code: string;
  strategy_version: string;
  strategy_fingerprint: string;
  profile_code: string;
  profile_version: string;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  signal_date: string;
  signal_decision_at: string;
  signal_result_fingerprint: string;
  entry_date: string;
  raw_entry_price: string;
  slipped_entry_price: string;
  entry_quantity: number;
  quantity: string;
  deployed_notional: string;
  entry_cost: BacktestCostBreakdown;
  stop_price: string | null;
  target_price: string | null;
  exit_date: string | null;
  raw_exit_price: string | null;
  slipped_exit_price: string | null;
  exit_reason: 'TIME_EXIT' | 'STOP_LOSS' | 'PROFIT_TARGET' | 'FORCED_END_OF_TEST' | 'EXIT_PRICE_UNAVAILABLE';
  exit_cost: BacktestCostBreakdown | null;
  holding_sessions: number;
  gross_pnl: string | null;
  total_costs: string | null;
  net_pnl: string | null;
  gross_return_pct: string | null;
  net_return_pct: string | null;
  maximum_favorable_excursion: string | null;
  maximum_adverse_excursion: string | null;
  corporate_action_events: Array<{ action_id: string; action_type: string; ex_date: string; ratio_numerator: string | null; ratio_denominator: string | null; quantity_before: string; quantity_after: string; reference_factor: string }>;
  warnings: string[];
  trade_fingerprint: string;
}

export interface BacktestAnalytics {
  signal_count: number;
  executable_setup_count: number;
  executed_trade_count: number;
  closed_trade_count: number;
  forced_end_count: number;
  closed_normal_count: number;
  winning_trades: number;
  losing_trades: number;
  breakeven_trades: number;
  win_rate: string | null;
  loss_rate: string | null;
  average_gross_return: string | null;
  average_net_return: string | null;
  median_net_return: string | null;
  average_gross_pnl: string | null;
  average_net_pnl: string | null;
  total_gross_pnl: string;
  total_net_pnl: string;
  total_transaction_costs: string;
  cost_drag_pct: string | null;
  average_transaction_cost_per_trade: string | null;
  profit_factor: string | null;
  expectancy_per_trade: string | null;
  best_trade_return: string | null;
  worst_trade_return: string | null;
  standard_deviation_net_returns: string | null;
  percentile_05: string | null;
  percentile_25: string | null;
  percentile_50: string | null;
  percentile_75: string | null;
  percentile_95: string | null;
  average_holding_sessions: string | null;
  maximum_holding_sessions: number | null;
  average_mae: string | null;
  average_mfe: string | null;
  warnings: string[];
}

export interface BacktestPeriodBreakdown {
  label: string;
  setup_count: number;
  trade_count: number;
  average_net_return: string | null;
  median_net_return: string | null;
  win_rate: string | null;
  profit_factor: string | null;
  total_net_pnl: string;
  total_costs: string;
}

export interface BacktestRunResponse {
  normalized_request: BacktestRunRequest;
  strategy: StrategyMetadata;
  effective_parameters: Record<string, StrategyScalar>;
  profile: BacktestProfileMetadata;
  cost_model: BacktestCostModelMetadata;
  universe: ScannerUniverse;
  dataset: { dataset_code: string; dataset_version: string; provider: string; security_count: number; price_row_count: number; corporate_action_revision_count: number; membership_interval_count: number; trading_session_count: number };
  config_fingerprint: string;
  dataset_fingerprint: string;
  run_fingerprint: string;
  historical_sessions_processed: number;
  setup_count: number;
  executable_setup_count: number;
  executed_trade_count: number;
  skipped_setup_count: number;
  skipped_setup_reasons: Record<string, number>;
  analytics: BacktestAnalytics;
  cost_analytics: { stt: string; exchange_transaction_charge: string; sebi_charge: string; gst: string; stamp_duty: string; brokerage: string; dp_charge: string; total: string; broker_specific_costs_excluded: boolean };
  yearly_breakdown: BacktestPeriodBreakdown[];
  holdout_breakdown: BacktestPeriodBreakdown[];
  warnings: string[];
  total_trade_count: number;
  returned_trade_count: number;
  trades_truncated: boolean;
  trade_order: 'ENTRY_DATE_SYMBOL_TRADE_ID_ASC';
  trades: BacktestTrade[];
  timings: { data_load_ms: number; feature_series_ms: number; condition_evaluation_ms: number; trade_simulation_ms: number; cost_and_analytics_ms: number; response_build_ms: number; total_service_ms: number };
  executed_at: string;
}

export interface PortfolioPolicyMetadata {
  policy_code: string;
  policy_version: string;
  display_name: string;
  compatible_profiles: string[];
  default_initial_capital_inr: string;
  default_max_concurrent_positions: number;
  default_max_position_weight: string;
  default_max_gross_exposure: string;
  default_minimum_cash_reserve_pct: string;
  allocation_policy: string;
  candidate_selection_policy: string;
  integer_share_policy: string;
  same_security_policy: string;
  same_session_event_order: string[];
  end_policy: string;
  mark_to_market_policy: string;
  risk_free_rate_convention: string;
  leverage_policy: string;
  rebalancing_policy: string;
  policy_fingerprint: string;
}

export interface PortfolioMetadata {
  policies: PortfolioPolicyMetadata[];
  profiles: BacktestProfileMetadata[];
  cost_models: BacktestCostModelMetadata[];
  strategies: StrategyMetadata[];
  universes: ScannerUniverse[];
  adjustment_policies: ('RAW' | 'ADJUSTED')[];
  parameter_bounds: Record<string, { minimum: string | number; maximum: string | number; default: string | number | null }>;
  allocation_semantics: Record<string, string>;
  metric_definitions: Record<string, string>;
  hard_request_limits: Record<string, number>;
  latest_observation_date: string | null;
  research_disclaimer: string;
}

export interface PortfolioRunRequest {
  strategy_code: string;
  strategy_version: string;
  parameter_overrides: Record<string, StrategyScalar>;
  universe: string;
  start_date: string;
  end_date: string;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  profile_code: string;
  profile_version: string;
  holding_sessions: number;
  slippage_bps: string;
  stop_loss_pct: string | null;
  profit_target_pct: string | null;
  max_exit_delay_sessions: number;
  cost_model_code: string;
  cost_model_version: string;
  brokerage_per_order_inr: string;
  brokerage_rate: string;
  dp_charge_per_scrip_sell_day_inr: string;
  portfolio_policy_code: string;
  portfolio_policy_version: string;
  initial_capital_inr: string;
  max_concurrent_positions: number;
  max_position_weight: string;
  max_gross_exposure: string;
  minimum_cash_reserve_pct: string;
  risk_free_rate_annual: string;
  out_of_sample_start_date: string | null;
  position_detail_limit: number;
  ledger_detail_limit: number;
}

export interface PortfolioCashLedgerEvent {
  sequence: number;
  ledger_event_id: string;
  session_date: string;
  security_id: string | null;
  symbol: string | null;
  event_type: 'INITIAL_CAPITAL' | 'ENTRY_PRINCIPAL' | 'ENTRY_COST' | 'EXIT_PROCEEDS' | 'EXIT_COST';
  gross_amount: string;
  cost_amount: string;
  net_cash_change: string;
  resulting_cash_balance: string;
  position_id: string | null;
  event_fingerprint: string;
}

export interface PortfolioRejectedCandidate {
  candidate_id: string;
  security_id: string;
  symbol: string;
  signal_date: string;
  intended_entry_date: string | null;
  reason: string;
  signal_fingerprint: string;
  candidate_fingerprint: string;
}

export interface PortfolioPosition {
  position_id: string;
  security_id: string;
  symbol: string;
  company_name: string;
  strategy_code: string;
  strategy_version: string;
  strategy_fingerprint: string;
  signal_date: string;
  signal_fingerprint: string;
  portfolio_policy_code: string;
  portfolio_policy_version: string;
  profile_code: string;
  profile_version: string;
  cost_model_code: string;
  cost_model_version: string;
  entry_date: string;
  entry_raw_price: string;
  entry_slipped_price: string;
  initial_quantity: number;
  current_quantity: string;
  entry_turnover: string;
  entry_costs: BacktestCostBreakdown;
  cost_basis: string;
  entry_portfolio_weight: string;
  stop_price: string | null;
  target_price: string | null;
  corporate_action_events: BacktestTrade['corporate_action_events'];
  exit_date: string | null;
  exit_raw_price: string | null;
  exit_slipped_price: string | null;
  exit_reason: 'TIME_EXIT' | 'STOP_LOSS' | 'PROFIT_TARGET' | 'FORCED_END_OF_TEST' | 'EXIT_PRICE_UNAVAILABLE';
  exit_turnover: string | null;
  exit_costs: BacktestCostBreakdown | null;
  gross_pnl: string | null;
  net_pnl: string | null;
  net_return: string | null;
  portfolio_contribution: string | null;
  holding_sessions: number;
  warnings: string[];
  position_fingerprint: string;
}

export interface DailyPortfolioSnapshot {
  session_date: string;
  cash: string;
  gross_market_value: string;
  portfolio_equity: string;
  realized_pnl_to_date: string;
  unrealized_pnl: string;
  daily_costs: string;
  cumulative_costs: string;
  open_position_count: number;
  gross_exposure_pct: string | null;
  cash_pct: string | null;
  daily_return: string | null;
  drawdown_pct: string | null;
  stale_mark_count: number;
  warnings: string[];
}

export interface PortfolioMetrics {
  initial_capital: string;
  ending_equity: string;
  net_portfolio_pnl: string;
  total_portfolio_return: string;
  cagr: string | null;
  annualized_volatility: string | null;
  sharpe_ratio: string | null;
  sortino_ratio: string | null;
  drawdown: { max_drawdown_pct: string | null; max_drawdown_inr: string | null; peak_date: string | null; trough_date: string | null; recovery_date: string | null; duration_sessions: number | null };
  calmar_ratio: string | null;
  average_gross_exposure: string | null;
  maximum_gross_exposure: string | null;
  average_cash_pct: string | null;
  minimum_cash: string;
  average_open_positions: string | null;
  maximum_open_positions: number;
  total_traded_turnover: string;
  portfolio_turnover: string | null;
  total_modeled_transaction_costs: string;
  cost_drag: string | null;
  entry_count: number;
  exit_count: number;
  profitable_positions: number;
  losing_positions: number;
  breakeven_positions: number;
  portfolio_win_rate: string | null;
  average_realized_position_return: string | null;
  median_realized_position_return: string | null;
  warnings: string[];
}

export interface PortfolioSegmentMetrics {
  label: 'IN_SAMPLE' | 'OUT_OF_SAMPLE';
  start_date: string | null;
  end_date: string | null;
  starting_equity: string | null;
  ending_equity: string | null;
  total_return: string | null;
  cagr: string | null;
  annualized_volatility: string | null;
  sharpe_ratio: string | null;
  sortino_ratio: string | null;
  max_drawdown_pct: string | null;
  warnings: string[];
}

export interface PortfolioRunResponse {
  normalized_request: PortfolioRunRequest;
  strategy: StrategyMetadata;
  effective_parameters: Record<string, StrategyScalar>;
  profile: BacktestProfileMetadata;
  cost_model: BacktestCostModelMetadata;
  portfolio_policy: PortfolioPolicyMetadata;
  universe: ScannerUniverse;
  dataset: BacktestRunResponse['dataset'];
  config_fingerprint: string;
  dataset_fingerprint: string;
  run_fingerprint: string;
  historical_sessions_processed: number;
  setup_count: number;
  accepted_entry_count: number;
  rejected_candidate_count: number;
  rejected_candidate_reasons: Record<string, number>;
  closed_position_count: number;
  metrics: PortfolioMetrics;
  cost_analytics: BacktestRunResponse['cost_analytics'];
  oos_metrics: PortfolioSegmentMetrics[];
  warnings: string[];
  daily_equity_curve: DailyPortfolioSnapshot[];
  total_position_count: number;
  returned_position_count: number;
  positions_truncated: boolean;
  position_order: 'ENTRY_DATE_SYMBOL_POSITION_ID_ASC';
  positions: PortfolioPosition[];
  total_ledger_event_count: number;
  returned_ledger_event_count: number;
  ledger_truncated: boolean;
  ledger_order: 'SESSION_SEQUENCE_ASC';
  ledger_events: PortfolioCashLedgerEvent[];
  returned_rejected_candidate_count: number;
  rejected_candidates_truncated: boolean;
  rejected_candidates: PortfolioRejectedCandidate[];
  timings: { data_load_ms: number; technical_series_ms: number; setup_generation_ms: number; allocation_and_accounting_ms: number; risk_analytics_ms: number; response_build_ms: number; total_service_ms: number };
  executed_at: string;
}

export interface HistoricalCompositionSourceRequest {
  inline_composition?: CompositionEvaluationRequest | null;
  experiment_id?: string | null;
}

export interface HistoricalAnalysisRequest {
  source: HistoricalCompositionSourceRequest;
  universe: string;
  start_date: string;
  end_date: string;
  adjustment_policy: 'RAW' | 'ADJUSTED';
  execution_policy_code: 'COMPOSITION_NEXT_OPEN_FIXED_HOLD';
  execution_policy_version: '1';
  holding_sessions: number;
  slippage_bps: string;
  stop_loss_pct: string | null;
  profit_target_pct: string | null;
  max_exit_delay_sessions: number;
  cost_model_code: 'INDIA_NSE_CASH_DELIVERY_2026_09';
  cost_model_version: '1';
  brokerage_per_order_inr: string;
  brokerage_rate: string;
  dp_charge_per_scrip_sell_day_inr: string;
  out_of_sample_start_date: string | null;
  diagnostic_detail_limit: number;
}

export interface CompositionBacktestRequest extends HistoricalAnalysisRequest {
  trade_notional_inr: string;
  trade_detail_limit: number;
}

export interface CompositionPortfolioRequest extends HistoricalAnalysisRequest {
  portfolio_policy_code: 'LONG_ONLY_EQUAL_SLOT_PORTFOLIO';
  portfolio_policy_version: '1';
  initial_capital_inr: string;
  max_concurrent_positions: number;
  max_position_weight: string;
  max_gross_exposure: string;
  minimum_cash_reserve_pct: string;
  risk_free_rate_annual: string;
  position_detail_limit: number;
  ledger_detail_limit: number;
}

export interface HistoricalCompositionSourceMetadata {
  source_type: 'INLINE_COMPOSITION' | 'SAVED_EXPERIMENT';
  experiment_id: string | null;
  experiment_name: string | null;
  experiment_description: string | null;
  composition_config_fingerprint: string;
  normalized_composition: CompositionEvaluationRequest;
  composition_definition_fields: string[];
  preserved_point_evaluation_fields: string[];
  historical_run_fields: string[];
}

export interface CompositionExecutionPolicyMetadata {
  policy_code: string;
  policy_version: string;
  display_name: string;
  backtest_profile_code: string;
  backtest_profile_version: string;
  signal_timing: string;
  entry_timing: string;
  exit_timing: string;
  direction: string;
  overlap_policy: string;
  default_holding_sessions: number;
  minimum_holding_sessions: number;
  maximum_holding_sessions: number;
  policy_fingerprint: string;
}

export interface HistoricalCompositionOutcome {
  security_id: string;
  symbol: string;
  observation_date: string;
  status: CompositionStatus;
  matched_strategy_count: number;
  insufficient_strategy_count: number;
  required_match_count: number;
  composition_result_fingerprint: string;
  components: Array<{
    strategy_code: string;
    strategy_version: string;
    status: CompositionStatus;
    result_fingerprint: string;
  }>;
}

export interface HistoricalSignalDiagnostics {
  eligible_evaluations: number;
  matched_setups: number;
  non_matches: number;
  insufficient_history: number;
  returned_outcomes: number;
  outcomes_truncated: boolean;
  outcome_order: 'DATE_SYMBOL_SECURITY_ASC';
  outcomes: HistoricalCompositionOutcome[];
}

export interface HistoricalResearchTimings {
  source_resolution_ms: number;
  universe_and_calendar_ms: number;
  data_load_ms: number;
  feature_generation_ms: number;
  composition_evaluation_ms: number;
  signal_fingerprint_ms: number;
  execution_ms: number;
  analytics_ms: number;
  response_build_ms: number;
  total_service_ms: number;
}

export interface CompositionBacktestResponse {
  normalized_request: CompositionBacktestRequest;
  source: HistoricalCompositionSourceMetadata;
  composition_policy: CompositionPolicyMetadata;
  components: CompositionComponentConfiguration[];
  execution_policy: CompositionExecutionPolicyMetadata;
  profile: BacktestProfileMetadata;
  cost_model: BacktestCostModelMetadata;
  universe: ScannerUniverse;
  dataset: BacktestRunResponse['dataset'];
  historical_signal_fingerprint: string;
  historical_dataset_fingerprint: string;
  backtest_config_fingerprint: string;
  backtest_run_fingerprint: string;
  engine_provenance: Record<string, unknown>;
  diagnostics: HistoricalSignalDiagnostics;
  skipped_setup_count: number;
  skipped_setup_reasons: Record<string, number>;
  executed_trade_count: number;
  analytics: BacktestAnalytics;
  cost_analytics: BacktestRunResponse['cost_analytics'];
  yearly_breakdown: BacktestPeriodBreakdown[];
  holdout_breakdown: BacktestPeriodBreakdown[];
  warnings: string[];
  total_trade_count: number;
  returned_trade_count: number;
  trades_truncated: boolean;
  trades: BacktestTrade[];
  timings: HistoricalResearchTimings;
  executed_at: string;
  research_disclaimer: string;
}

export interface CompositionPortfolioResponse {
  normalized_request: CompositionPortfolioRequest;
  source: HistoricalCompositionSourceMetadata;
  composition_policy: CompositionPolicyMetadata;
  components: CompositionComponentConfiguration[];
  execution_policy: CompositionExecutionPolicyMetadata;
  profile: BacktestProfileMetadata;
  cost_model: BacktestCostModelMetadata;
  portfolio_policy: PortfolioPolicyMetadata;
  universe: ScannerUniverse;
  dataset: BacktestRunResponse['dataset'];
  historical_signal_fingerprint: string;
  historical_dataset_fingerprint: string;
  portfolio_config_fingerprint: string;
  portfolio_run_fingerprint: string;
  engine_provenance: Record<string, unknown>;
  diagnostics: HistoricalSignalDiagnostics;
  accepted_entry_count: number;
  rejected_candidate_count: number;
  rejected_candidate_reasons: Record<string, number>;
  closed_position_count: number;
  metrics: PortfolioMetrics;
  cost_analytics: BacktestRunResponse['cost_analytics'];
  oos_metrics: PortfolioSegmentMetrics[];
  warnings: string[];
  daily_equity_curve: DailyPortfolioSnapshot[];
  total_position_count: number;
  returned_position_count: number;
  positions_truncated: boolean;
  positions: PortfolioPosition[];
  total_ledger_event_count: number;
  returned_ledger_event_count: number;
  ledger_truncated: boolean;
  ledger_events: PortfolioCashLedgerEvent[];
  returned_rejected_candidate_count: number;
  rejected_candidates_truncated: boolean;
  rejected_candidates: PortfolioRejectedCandidate[];
  timings: HistoricalResearchTimings;
  executed_at: string;
  research_disclaimer: string;
}
