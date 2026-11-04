export interface ResearchForecast {
  available: boolean;
  reason?: string;
  model?: string;
  projected_score?: { home: number; away: number; total: number };
  intervals?: { home: { low: number; high: number }; away: { low: number; high: number } };
  notice?: string;
  feature_coverage?: number;
  imputed_features?: string[];
  validation?: { status: string; test_games: number; points_mae: number; baseline_mae: number; interval_coverage: { home: number; away: number }; test_end: string };
  references?: { title: string; url: string }[];
}

export function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function numericFields(value: unknown, keys: string[]): boolean {
  return record(value) && keys.every(key => typeof value[key] === 'number' && Number.isFinite(value[key]));
}

export function validResearch(value: unknown): boolean {
  if (value == null) return true;
  if (!record(value) || typeof value.available !== 'boolean') return false;
  if (!value.available) return typeof value.reason === 'string';
  return typeof value.model === 'string' && typeof value.notice === 'string'
    && numericFields(value.projected_score, ['home', 'away', 'total'])
    && record(value.intervals) && ['home', 'away'].every(side => numericFields((value.intervals as Record<string, unknown>)[side], ['low', 'high']))
    && record(value.validation) && numericFields(value.validation, ['test_games', 'points_mae', 'baseline_mae'])
    && typeof value.validation.test_end === 'string' && numericFields(value.validation.interval_coverage, ['home', 'away'])
    && Array.isArray(value.references) && value.references.every(item => record(item) && typeof item.title === 'string' && typeof item.url === 'string' && item.url.startsWith('https://'));
}
