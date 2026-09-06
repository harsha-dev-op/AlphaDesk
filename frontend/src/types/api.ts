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
