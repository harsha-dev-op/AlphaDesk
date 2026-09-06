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
