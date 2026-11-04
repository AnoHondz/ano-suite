import type { Decision } from './components/DecisionCard';

export function slatePlan(cards: Decision[]) {
  return {
    bet: cards.filter(card => card.action === 'BET').length,
    consider: cards.filter(card => card.action === 'CONSIDER').length,
    skip: cards.filter(card => card.action === 'SKIP').length,
  };
}
