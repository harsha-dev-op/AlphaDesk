import type { BacktestMetadata, BacktestRunRequest, BacktestRunResponse, CompositionBacktestRequest, CompositionBacktestResponse, CompositionEvaluationRequest, CompositionEvaluationResponse, CompositionMetadata, CompositionPortfolioRequest, CompositionPortfolioResponse, DataCoverageResponse, DataSourcesResponse, ExperimentCreateRequest, ExperimentCreateResponse, ExperimentDefinition, ExperimentListResponse, ExperimentReplayResponse, ExperimentRunListResponse, FeatureCatalog, FeatureSetCatalog, HealthResponse, MarketScanRequest, MarketScanResponse, PortfolioMetadata, PortfolioRunRequest, PortfolioRunResponse, PriceSeries, QualityResponse, ScannerMetadata, SecuritiesPage, Security, SecurityFeatureSeries, StrategyCatalog, StrategyEvaluationRequest, StrategyEvaluationResponse } from '@/src/types/api';

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

async function request<T>(path: string, signal?: AbortSignal, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('Accept', 'application/json');
  if (init?.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    signal,
    headers,
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json() as { detail?: string | Array<{ msg?: string }> };
      if (typeof body.detail === 'string') detail = body.detail;
      else if (Array.isArray(body.detail) && body.detail[0]?.msg) detail = body.detail[0].msg;
    } catch {
      // Keep the status-based message when the backend does not return JSON.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: (signal?: AbortSignal) => request<HealthResponse>('/health', signal),
  securities: (page = 1, pageSize = 25, query = '', signal?: AbortSignal) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (query) params.set('q', query);
    return request<SecuritiesPage>(`/api/v1/securities?${params}`, signal);
  },
  quality: (signal?: AbortSignal) => request<QualityResponse>('/api/v1/data-quality/status', signal),
  dataSources: (signal?: AbortSignal) => request<DataSourcesResponse>('/api/v1/data-sources', signal),
  dataCoverage: (signal?: AbortSignal) => request<DataCoverageResponse>('/api/v1/data-coverage', signal),
  security: (symbol: string, signal?: AbortSignal) => request<Security>(`/api/v1/securities/${encodeURIComponent(symbol)}`, signal),
  prices: (symbol: string, view: 'raw' | 'adjusted', signal?: AbortSignal) => request<PriceSeries>(`/api/v1/securities/${encodeURIComponent(symbol)}/prices?view=${view}`, signal),
  featureCatalog: (signal?: AbortSignal) => request<FeatureCatalog>('/api/v1/features/catalog', signal),
  featureSets: (signal?: AbortSignal) => request<FeatureSetCatalog>('/api/v1/feature-sets', signal),
  features: (symbol: string, adjustmentPolicy: 'raw' | 'adjusted' = 'adjusted', signal?: AbortSignal) => request<SecurityFeatureSeries>(`/api/v1/securities/${encodeURIComponent(symbol)}/features?adjustment_policy=${adjustmentPolicy}`, signal),
  scannerMetadata: (signal?: AbortSignal) => request<ScannerMetadata>('/api/v1/scanner/metadata', signal),
  scanMarket: (payload: MarketScanRequest, signal?: AbortSignal) => request<MarketScanResponse>('/api/v1/scanner/scan', signal, { method: 'POST', body: JSON.stringify(payload) }),
  strategyCatalog: (signal?: AbortSignal) => request<StrategyCatalog>('/api/v1/strategies/catalog', signal),
  evaluateStrategy: (payload: StrategyEvaluationRequest, signal?: AbortSignal) => request<StrategyEvaluationResponse>('/api/v1/strategies/evaluate', signal, { method: 'POST', body: JSON.stringify(payload) }),
  backtestMetadata: (signal?: AbortSignal) => request<BacktestMetadata>('/api/v1/backtests/metadata', signal),
  runBacktest: (payload: BacktestRunRequest, signal?: AbortSignal) => request<BacktestRunResponse>('/api/v1/backtests/run', signal, { method: 'POST', body: JSON.stringify(payload) }),
  portfolioMetadata: (signal?: AbortSignal) => request<PortfolioMetadata>('/api/v1/portfolio/metadata', signal),
  runPortfolio: (payload: PortfolioRunRequest, signal?: AbortSignal) => request<PortfolioRunResponse>('/api/v1/portfolio/run', signal, { method: 'POST', body: JSON.stringify(payload) }),
  researchMetadata: (signal?: AbortSignal) => request<CompositionMetadata>('/api/v1/research/compositions/metadata', signal),
  evaluateComposition: (payload: CompositionEvaluationRequest, signal?: AbortSignal) => request<CompositionEvaluationResponse>('/api/v1/research/compositions/evaluate', signal, { method: 'POST', body: JSON.stringify(payload) }),
  createExperiment: (payload: ExperimentCreateRequest, signal?: AbortSignal) => request<ExperimentCreateResponse>('/api/v1/research/experiments', signal, { method: 'POST', body: JSON.stringify(payload) }),
  experiments: (page = 1, pageSize = 25, signal?: AbortSignal) => request<ExperimentListResponse>(`/api/v1/research/experiments?page=${page}&page_size=${pageSize}`, signal),
  experiment: (id: string, signal?: AbortSignal) => request<ExperimentDefinition>(`/api/v1/research/experiments/${encodeURIComponent(id)}`, signal),
  experimentRuns: (id: string, page = 1, pageSize = 25, signal?: AbortSignal) => request<ExperimentRunListResponse>(`/api/v1/research/experiments/${encodeURIComponent(id)}/runs?page=${page}&page_size=${pageSize}`, signal),
  replayExperiment: (id: string, signal?: AbortSignal) => request<ExperimentReplayResponse>(`/api/v1/research/experiments/${encodeURIComponent(id)}/runs`, signal, { method: 'POST' }),
  runCompositionBacktest: (payload: CompositionBacktestRequest, signal?: AbortSignal) => request<CompositionBacktestResponse>('/api/v1/research/backtest', signal, { method: 'POST', body: JSON.stringify(payload) }),
  runCompositionPortfolio: (payload: CompositionPortfolioRequest, signal?: AbortSignal) => request<CompositionPortfolioResponse>('/api/v1/research/portfolio', signal, { method: 'POST', body: JSON.stringify(payload) }),
};
