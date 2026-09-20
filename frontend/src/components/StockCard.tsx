import type { Stock } from '@/lib/stocks';

type Props = {
  stock: Stock;
  busy: boolean;
  onToggle: (stock: Stock) => void;
};

/** One stock. React renders every value here as text, so nothing from the server is ever HTML. */
export function StockCard({ stock, busy, onToggle }: Props) {
  const action = stock.followed ? 'Unfollow' : 'Follow';
  return (
    <article className="card">
      <h2>{stock.name}</h2>
      <p className="symbol">{stock.symbol}</p>
      <p className="muted">BSE {stock.bse_code}</p>
      <p className="muted">{stock.sector}</p>
      <button
        type="button"
        className={stock.followed ? 'button secondary' : 'button'}
        aria-pressed={stock.followed}
        aria-label={`${action} ${stock.symbol}`}
        disabled={busy}
        onClick={() => onToggle(stock)}
      >
        {stock.followed ? 'Following' : 'Follow'}
      </button>
    </article>
  );
}
