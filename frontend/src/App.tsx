import { useCallback, useMemo, useState } from 'react';
import './App.css';
import { createApiClient } from './api';
import { Login, type AuthSession } from './components/Login';
import { Marketplace } from './components/Marketplace';
import { PlatformDashboard } from './components/PlatformDashboard';
import { VoicePanel } from './components/VoicePanel';

const SESSION_EXPIRED_NOTICE = 'Your session expired. Please sign in again.';

function App() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [signedOutNotice, setSignedOutNotice] = useState('');
  const [selectedDistributorId, setSelectedDistributorId] = useState<string | null>(null);
  const [isVoicePanelOpen, setIsVoicePanelOpen] = useState(false);
  const [enquiryRefreshVersion, setEnquiryRefreshVersion] = useState(0);

  const accessToken = session?.accessToken ?? '';

  const endSession = useCallback((notice: string) => {
    setSession(null);
    setSignedOutNotice(notice);
    setIsVoicePanelOpen(false);
    setSelectedDistributorId(null);
  }, []);

  // Rebuilt only when the token changes, so panels do not see a new client on
  // every render and refetch in a loop.
  const api = useMemo(
    () => createApiClient(accessToken, () => endSession(SESSION_EXPIRED_NOTICE)),
    [accessToken, endSession],
  );

  const context = useMemo(
    () => ({
      buyer: {
        id: session?.user.id ?? '',
        name: session?.user.name ?? '',
        company: session?.user.workspace_name ?? '',
      },
      current_page: 'distributor_directory',
      selected_distributor_id: selectedDistributorId,
    }),
    [session?.user.id, session?.user.name, session?.user.workspace_name, selectedDistributorId],
  );

  if (!session) {
    return (
      <Login
        notice={signedOutNotice}
        onAuthenticated={(authenticated) => {
          setSignedOutNotice('');
          setSession(authenticated);
        }}
      />
    );
  }

  return (
    <div className="app-container">
      <div className="workspace-bar">
        <span className="workspace-brand">
          <strong>TradeVoice</strong>
          <span>{session.user.workspace_name}</span>
        </span>
        <span className="workspace-user">
          {session.user.name} · {session.user.role}
          <button onClick={() => endSession('')}>Sign out</button>
        </span>
      </div>
      <PlatformDashboard api={api} refreshVersion={enquiryRefreshVersion} />
      <Marketplace
        api={api}
        user={session.user}
        selectedId={selectedDistributorId}
        onSelect={setSelectedDistributorId}
        enquiryRefreshVersion={enquiryRefreshVersion}
      />
      {isVoicePanelOpen ? (
        <VoicePanel
          context={context}
          api={api}
          onClose={() => setIsVoicePanelOpen(false)}
          onEnquiryCreated={() => setEnquiryRefreshVersion((version) => version + 1)}
        />
      ) : (
        <button className="floating-btn" onClick={() => setIsVoicePanelOpen(true)}>
          Talk to Support
        </button>
      )}
    </div>
  );
}

export default App;
