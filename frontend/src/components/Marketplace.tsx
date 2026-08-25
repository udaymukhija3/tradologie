import React, { useEffect, useState } from 'react';
import { ApiError, type ApiClient } from '../api';
import type { AuthUser } from './Login';

export interface Distributor {
  id: string;
  name: string;
  location: string;
  categories: string[];
  status: string;
}

interface Enquiry {
  id: string;
  display_id: string;
  product: string;
  quantity: number;
  unit: string;
  destination: string;
  status: string;
}

interface Props {
  api: ApiClient;
  user: AuthUser;
  selectedId: string | null;
  onSelect: (id: string) => void;
  enquiryRefreshVersion: number;
}

export const Marketplace: React.FC<Props> = ({ api, user, selectedId, onSelect, enquiryRefreshVersion }) => {
  const [distributors, setDistributors] = useState<Distributor[]>([]);
  const [enquiries, setEnquiries] = useState<Enquiry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;

    Promise.all([
      api.get<Distributor[]>('/api/distributors', { signal }),
      api.get<Enquiry[]>('/api/enquiries', { signal }),
    ])
      .then(([distributorData, enquiryData]) => {
        setDistributors(distributorData);
        setEnquiries(enquiryData);
        setError('');
      })
      .catch((requestError: unknown) => {
        if (ApiError.isAbort(requestError)) return;
        if (requestError instanceof ApiError && requestError.status === 401) return;
        setError('TradeVoice could not load marketplace data. Confirm that the backend is running.');
      })
      .finally(() => setIsLoading(false));

    return () => controller.abort();
  }, [api, enquiryRefreshVersion]);

  return (
    <div className="marketplace">
      <div className="header">
        <h1>TradeVoice Marketplace</h1>
        <div className="user-info">
          {user.name} · {user.workspace_name}
        </div>
      </div>

      <section className="enquiry-section" aria-labelledby="recent-enquiries-heading">
        <div className="section-heading-row">
          <div>
            <h2 id="recent-enquiries-heading">Recent enquiries</h2>
            <p>New confirmed enquiries appear here immediately.</p>
          </div>
          <span className="count-badge">{enquiries.length}</span>
        </div>
        <div className="enquiry-list">
          {enquiries.slice(0, 4).map((enquiry) => (
            <article className="enquiry-card" key={enquiry.id}>
              <div>
                <strong>{enquiry.display_id}</strong>
                <span>{enquiry.status}</span>
              </div>
              <p>{enquiry.quantity} {enquiry.unit} · {enquiry.product}</p>
              <p>Destination: {enquiry.destination}</p>
            </article>
          ))}
        </div>
      </section>

      <div className="section-heading-row distributors-heading">
        <div>
          <h2>Verified distributor directory</h2>
          <p>Select a distributor to inject page context into support.</p>
        </div>
      </div>

      {isLoading && <div className="data-state">Loading marketplace data…</div>}
      {error && <div className="data-state error-state" role="alert">{error}</div>}

      <div className="distributor-list">
        {distributors.map(dist => (
          <button
            type="button"
            key={dist.id}
            className={`distributor-card ${selectedId === dist.id ? 'selected' : ''}`}
            onClick={() => onSelect(dist.id)}
            aria-pressed={selectedId === dist.id}
          >
            <h3>{dist.name}</h3>
            <p>Location: {dist.location}</p>
            <p>Status: {dist.status}</p>
            <div className="tags">
              {dist.categories.map(cat => (
                <span key={cat} className="tag">{cat}</span>
              ))}
            </div>
            {selectedId === dist.id && (
              <p style={{marginTop: '1rem', color: '#3b82f6', fontWeight: 500}}>Selected</p>
            )}
          </button>
        ))}
      </div>
    </div>
  );
};
