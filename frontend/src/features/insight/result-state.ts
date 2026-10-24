import type { GeneratedInsight } from '@/shared/api/types';

export function hasInsightContent(result: GeneratedInsight): boolean {
  const { presentation } = result;
  const statements = new Set(
    [
      ...(presentation.facts ?? []),
      ...(presentation.bank_position ?? []),
      ...(presentation.ai_conclusions ?? []),
      ...(presentation.client_context ?? []),
    ]
      .filter((row) => row.statement.text.trim())
      .map((row) => row.statement.id),
  );
  return presentation.blocks.some(
    (block) =>
      block.id !== 'basis' &&
      ((block.statement_ids ?? []).some((id) => statements.has(id)) ||
        (block.id === 'actions' && Boolean(result.offer))),
  );
}

export function insightResultState(result: GeneratedInsight) {
  if (result.insight.status === 'no_significant_link') return 'unrelated';
  if (result.insight.status === 'insufficient_data') return 'insufficient';
  if (result.execution_status !== 'completed') return 'partial';
  return hasInsightContent(result) ? 'ready' : 'empty';
}
