import { useState } from 'react';
import './App.css';
import { Login, type AuthSession } from './components/Login';
import { Marketplace } from './components/Marketplace';
import { PlatformDashboard } from './components/PlatformDashboard';
import { VoicePanel } from './components/VoicePanel';

function App() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [selectedDistributorId, setSelectedDistributorId] = useState<string | null>(null);
  const [isVoicePanelOpen, setIsVoicePanelOpen] = useState(false);
  const [enquiryRefreshVersion, setEnquiryRefreshVersion] = useState(0);

  if (!session) return <Login onAuthenticated={setSession} />;

  const context = {
    buyer: { id: session.user.id, name: session.user.name, company: session.user.workspace_name },
    current_page: 'distributor_directory',
    selected_distributor_id: selectedDistributorId,
  };

  return (
    <div className="app-container">
      <div className="workspace-bar"><span className="workspace-brand"><strong>TradeVoice</strong><span>{session.user.workspace_name}</span></span><span className="workspace-user">{session.user.name} · {session.user.role}<button onClick={() => setSession(null)}>Sign out</button></span></div>
      <PlatformDashboard accessToken={session.accessToken} refreshVersion={enquiryRefreshVersion} />
      <Marketplace accessToken={session.accessToken} user={session.user} selectedId={selectedDistributorId} onSelect={setSelectedDistributorId} enquiryRefreshVersion={enquiryRefreshVersion} />
      {isVoicePanelOpen ? (
        <VoicePanel context={context} accessToken={session.accessToken} onClose={() => setIsVoicePanelOpen(false)} onEnquiryCreated={() => setEnquiryRefreshVersion((version) => version + 1)} />
      ) : (
        <button className="floating-btn" onClick={() => setIsVoicePanelOpen(true)}>Talk to Support</button>
      )}
    </div>
  );
}

export default App;
