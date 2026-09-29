import { Monogram } from '@/components/Monogram';
import { stockPageHref } from '@/lib/insights';
import type { Stock } from '@/lib/stocks';

type Props = {
  stock: Stock;
  busy: boolean;
  onToggle: (stock: Stock) => void;
};

/**
 * One row of the watchlist. React renders every value here as text, so nothing from the server
 * is ever HTML. There is no price: the app holds none (ADR 007).
 */
export function StockCard({ stock, busy, onToggle }: Props) {
  const action = stock.followed ? 'Unfollow' : 'Follow';
  return (
    <li className="watch-row">
      <Monogram symbol={stock.symbol} />
      <div className="watch-id">
        <h2>{stock.symbol}</h2>
        <p className="muted">{stock.name}</p>
      </div>
      <div className="watch-meta">
        <p>BSE {stock.bse_code}</p>
        <p>{stock.sector}</p>
      </div>
      <div className="watch-actions">
        <a
          href={stockPageHref(stock.symbol)}
          className="stock-link"
          aria-label={`Key facts for ${stock.symbol}`}
        >
          Key facts
        </a>
        <button
          type="button"
          className={stock.followed ? 'button soft' : 'button'}
          aria-pressed={stock.followed}
          aria-label={`${action} ${stock.symbol}`}
          disabled={busy}
          onClick={() => onToggle(stock)}
        >
          {stock.followed ? 'Following' : 'Follow'}
        </button>
      </div>
    </li>
  );
}
