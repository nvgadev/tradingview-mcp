/**
 * CME micro futures contract specifications.
 *
 * `pointValue` is USD per 1.0 point of price movement; `tickSize` is the
 * minimum price increment. Commission is a per-contract round-turn estimate —
 * it varies by broker and plan, so treat the default as a placeholder and
 * override it with your own rate before trusting any P&L figure.
 */
export const CONTRACTS = {
  MES: { name: 'Micro E-mini S&P 500', tickSize: 0.25, pointValue: 5.0, commissionRT: 1.24 },
  MNQ: { name: 'Micro E-mini Nasdaq-100', tickSize: 0.25, pointValue: 2.0, commissionRT: 1.24 },
  MYM: { name: 'Micro E-mini Dow', tickSize: 1.0, pointValue: 0.5, commissionRT: 1.24 },
  M2K: { name: 'Micro E-mini Russell 2000', tickSize: 0.1, pointValue: 5.0, commissionRT: 1.24 },
  MGC: { name: 'Micro Gold', tickSize: 0.1, pointValue: 10.0, commissionRT: 1.54 },
  MCL: { name: 'Micro Crude Oil', tickSize: 0.01, pointValue: 100.0, commissionRT: 1.54 },
};

/**
 * @param {string} symbol e.g. 'MES'
 * @returns {{name:string,tickSize:number,pointValue:number,commissionRT:number}}
 */
export function getContract(symbol) {
  const spec = CONTRACTS[String(symbol).toUpperCase()];
  if (!spec) {
    throw new Error(`Unknown contract '${symbol}'. Known: ${Object.keys(CONTRACTS).join(', ')}`);
  }
  return spec;
}

/**
 * Round a price to the contract's tick grid.
 * @param {number} price
 * @param {number} tickSize
 * @returns {number}
 */
export function roundToTick(price, tickSize) {
  return Math.round(price / tickSize) * tickSize;
}
