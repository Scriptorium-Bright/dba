import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiRequest } from './api.js';

const USERS = [
  { id: 101, name: '하늘' },
  { id: 102, name: '서윤' },
  { id: 103, name: '도윤' },
];
// P3 실습 화면은 별도 Vite 프로세스에서만 켜 P2 화면의 기본 흐름을 보존한다.
const P3_COMPENSATION_ENABLED = import.meta.env.VITE_P3_COMPENSATION_ENABLED === 'true';

const NAV_ITEMS = [
  { id: 'events', label: '이벤트', mark: '01' },
  { id: 'wallet', label: '내 지갑', mark: '02' },
  { id: 'admin', label: '운영 관리', mark: '03', admin: true },
];

const numberFormat = new Intl.NumberFormat('ko-KR');
const dateFormat = new Intl.DateTimeFormat('ko-KR', {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
});

function formatAmount(value) {
  return numberFormat.format(value ?? 0);
}

function formatDate(value) {
  if (!value) return '예약 없음';
  return dateFormat.format(new Date(value));
}

function localInputDate(offsetDays = 0, hour = 9) {
  const date = new Date();
  date.setDate(date.getDate() + offsetDays);
  date.setHours(hour, 0, 0, 0);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}

function statusLabel(status) {
  return { DRAFT: '준비 중', OPEN: '진행 중', CLOSED: '종료' }[status] || status;
}

function getClaimAvailability(event) {
  if (!event || event.mode !== 'CLAIM' || event.status !== 'OPEN' || event.claimed) return false;
  const now = Date.now();
  return now >= new Date(event.startsAt).getTime() && now < new Date(event.endsAt).getTime();
}

function App() {
  const [role, setRole] = useState(() => localStorage.getItem('reward-demo-role') || 'USER');
  const [userId, setUserId] = useState(() => Number(localStorage.getItem('reward-demo-user') || 101));
  const [view, setView] = useState('events');
  const [events, setEvents] = useState([]);
  const [wallet, setWallet] = useState(null);
  const [ledger, setLedger] = useState([]);
  const [selectedEventId, setSelectedEventId] = useState(null);
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [loading, setLoading] = useState(true);
  const [connected, setConnected] = useState(false);
  const [pageError, setPageError] = useState('');
  const [notice, setNotice] = useState(null);
  const [claimFeedback, setClaimFeedback] = useState(null);
  const [claimBusy, setClaimBusy] = useState(false);
  const [formBusy, setFormBusy] = useState(false);
  const [batchBusy, setBatchBusy] = useState({});
  const [batchDistributions, setBatchDistributions] = useState({});
  const [batchRecipientText, setBatchRecipientText] = useState({});
  const [myWalletResetBusy, setMyWalletResetBusy] = useState(false);
  const [myWalletResetConfirm, setMyWalletResetConfirm] = useState(false);
  const [allWalletResetBusy, setAllWalletResetBusy] = useState(false);
  const [allWalletResetConfirm, setAllWalletResetConfirm] = useState(false);
  const [allWalletResetPhrase, setAllWalletResetPhrase] = useState('');
  const [compensationBusy, setCompensationBusy] = useState(false);
  const [payoutRows, setPayoutRows] = useState([]);
  const [payoutSearchBusy, setPayoutSearchBusy] = useState(false);
  const [payoutCursor, setPayoutCursor] = useState(null);
  const [payoutHasMore, setPayoutHasMore] = useState(false);
  const [payoutFilters, setPayoutFilters] = useState({ userId: '', sourceType: '', from: '', to: '' });
  const [formMode, setFormMode] = useState('CLAIM');
  const retryKeys = useRef(new Map());

  const actor = useMemo(() => ({ role, userId }), [role, userId]);

  const refreshData = useCallback(async () => {
    setLoading(true);
    setPageError('');
    try {
      const [eventRows, walletRow, ledgerRows] = await Promise.all([
        apiRequest('/events', actor),
        apiRequest('/me/wallet', actor),
        apiRequest('/me/ledger?limit=20', actor),
      ]);
      const distributionRows = role === 'ADMIN'
        ? await Promise.all(eventRows.filter((event) => event.mode === 'BATCH').map(async (event) => [
          event.id,
          await apiRequest(`/admin/events/${event.id}/distribution`, actor),
        ]))
        : [];
      const nextDistributions = Object.fromEntries(distributionRows);
      setEvents(eventRows);
      setWallet(walletRow);
      setLedger(ledgerRows);
      setBatchDistributions(nextDistributions);
      setBatchRecipientText(Object.fromEntries(Object.entries(nextDistributions).map(([id, item]) => [
        id, item.recipientUserIds.join(', '),
      ])));
      setConnected(true);
      setSelectedEventId((current) => current && eventRows.some((event) => event.id === current)
        ? current
        : (eventRows[0]?.id ?? null));
    } catch (error) {
      setConnected(false);
      setPageError(error.message);
    } finally {
      setLoading(false);
    }
  }, [actor]);

  useEffect(() => {
    void refreshData();
  }, [refreshData]);

  useEffect(() => {
    localStorage.setItem('reward-demo-role', role);
    if (role !== 'ADMIN' && view === 'admin') setView('events');
  }, [role, view]);

  useEffect(() => {
    localStorage.setItem('reward-demo-user', String(userId));
  }, [userId]);

  useEffect(() => {
    if (!notice) return undefined;
    const timeout = window.setTimeout(() => setNotice(null), 4200);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  const selectedEvent = events.find((event) => event.id === selectedEventId) || null;
  const visibleEvents = statusFilter === 'ALL'
    ? events
    : events.filter((event) => event.status === statusFilter);
  const openEvents = events.filter((event) => event.status === 'OPEN');
  const claimableCount = events.filter(getClaimAvailability).length;
  const batchCount = events.filter((event) => event.mode === 'BATCH').length;
  const selectedUser = USERS.find((user) => user.id === userId);
  const pageTitle = view === 'wallet' ? '내 지갑' : view === 'admin' ? '운영 관리' : '이벤트 보상';

  async function handleClaim(event) {
    if (!event || claimBusy) return;
    const retryKey = `${userId}:${event.id}`;
    let requestKey = retryKeys.current.get(retryKey);
    if (!requestKey) {
      requestKey = crypto.randomUUID();
      retryKeys.current.set(retryKey, requestKey);
    }
    setClaimBusy(true);
    setClaimFeedback(null);
    try {
      const result = await apiRequest(`/events/${event.id}/claims`, actor, {
        method: 'POST',
        headers: { 'Idempotency-Key': requestKey },
      });
      retryKeys.current.delete(retryKey);
      const styles = {
        PAID: 'success', REPLAYED: 'success', ALREADY_GRANTED: 'neutral', NOT_AVAILABLE: 'warning',
      };
      setClaimFeedback({ tone: styles[result.result] || 'neutral', message: result.message });
      await refreshData();
    } catch (error) {
      setClaimFeedback({ tone: 'warning', message: `${error.message} 결과가 불확실하면 같은 버튼으로 다시 확인하세요.` });
    } finally {
      setClaimBusy(false);
    }
  }

  async function createEvent(event) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const mode = String(form.get('mode'));
    const startsAt = String(form.get('startsAt'));
    const endsAt = String(form.get('endsAt'));
    const scheduledAt = String(form.get('scheduledAt') || '');
    const payload = {
      name: String(form.get('name')).trim(),
      mode,
      startsAt: new Date(startsAt).toISOString(),
      endsAt: new Date(endsAt).toISOString(),
      scheduledAt: mode === 'BATCH' && scheduledAt ? new Date(scheduledAt).toISOString() : null,
      rewardMoney: Number(form.get('rewardMoney')),
      rewardPoint: Number(form.get('rewardPoint')),
    };
    setFormBusy(true);
    try {
      const created = await apiRequest('/admin/events', actor, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      await refreshData();
      setSelectedEventId(created.id);
      setNotice({ tone: 'success', message: '새 이벤트를 DRAFT로 등록했습니다.' });
      formElement.reset();
      setFormMode('CLAIM');
    } catch (error) {
      setNotice({ tone: 'error', message: error.message });
    } finally {
      setFormBusy(false);
    }
  }

  async function changeStatus(event, action) {
    const endpoint = action === 'OPEN' ? 'open' : 'close';
    try {
      await apiRequest(`/admin/events/${event.id}/${endpoint}`, actor, { method: 'POST' });
      await refreshData();
      setNotice({
        tone: 'success',
        message: action === 'OPEN' ? '이벤트를 오픈했습니다.' : '이벤트를 종료했습니다.',
      });
    } catch (error) {
      setNotice({ tone: 'error', message: error.message });
    }
  }

  async function replaceBatchRecipients(formEvent, eventId) {
    formEvent.preventDefault();
    const rawIds = new FormData(formEvent.currentTarget).get('userIds');
    const userIds = String(rawIds || '').split(/[\s,]+/).filter(Boolean).map(Number);
    setBatchBusy((current) => ({ ...current, [eventId]: true }));
    try {
      const result = await apiRequest(`/admin/events/${eventId}/recipients`, actor, {
        method: 'PUT',
        body: JSON.stringify({ userIds }),
      });
      setNotice({ tone: 'success', message: `${formatAmount(result.recipientCount)}명의 대상 명단을 저장했습니다.` });
      await refreshData();
    } catch (error) {
      setNotice({ tone: 'error', message: error.message });
    } finally {
      setBatchBusy((current) => ({ ...current, [eventId]: false }));
    }
  }

  async function runBatch(eventId) {
    setBatchBusy((current) => ({ ...current, [eventId]: true }));
    try {
      const result = await apiRequest(`/admin/events/${eventId}/run`, actor, {
        method: 'POST',
        body: JSON.stringify({ maxRecipients: 1000 }),
      });
      setNotice({
        tone: result.status === 'COMPLETED' ? 'success' : 'error',
        message: `배치 ${result.status === 'COMPLETED' ? '완료' : '확인 필요'} · 지급 ${formatAmount(result.paidCount)}명 · 실패 ${formatAmount(result.failedCount)}명`,
      });
      await refreshData();
    } catch (error) {
      setNotice({ tone: 'error', message: error.message });
    } finally {
      setBatchBusy((current) => ({ ...current, [eventId]: false }));
    }
  }

  async function resetMyWallet() {
    if (myWalletResetBusy) return;
    setMyWalletResetBusy(true);
    try {
      const result = await apiRequest('/me/wallet/reset', actor, { method: 'POST' });
      setMyWalletResetConfirm(false);
      setNotice({
        tone: 'success',
        message: `내 지갑 초기화 완료 · 이벤트 지급 ${formatAmount(result.grantsDeleted)}건, 장애 보상 ${formatAmount(result.compensationsDeleted ?? 0)}건, 원장 ${formatAmount(result.ledgerRowsDeleted)}건을 정리했습니다.`,
      });
      await refreshData();
    } catch (error) {
      setNotice({ tone: 'error', message: `내 지갑을 초기화하지 못했습니다 · ${error.message}` });
    } finally {
      setMyWalletResetBusy(false);
    }
  }

  async function resetAllWallets() {
    if (allWalletResetBusy || allWalletResetPhrase.trim() !== '전체 초기화') return;
    setAllWalletResetBusy(true);
    try {
      const result = await apiRequest('/admin/wallets/reset-all', actor, { method: 'POST' });
      setAllWalletResetConfirm(false);
      setAllWalletResetPhrase('');
      setNotice({
        tone: 'success',
        message: `전체 사용자 초기화 완료 · ${formatAmount(result.usersReset)}명, 이벤트 지급 ${formatAmount(result.grantsDeleted)}건, 장애 보상 ${formatAmount(result.compensationsDeleted ?? 0)}건, 원장 ${formatAmount(result.ledgerRowsDeleted)}건을 정리했습니다.`,
      });
      await refreshData();
    } catch (error) {
      setNotice({ tone: 'error', message: `전체 사용자를 초기화하지 못했습니다 · ${error.message}` });
    } finally {
      setAllWalletResetBusy(false);
    }
  }

  async function submitCompensation(formEvent) {
    formEvent.preventDefault();
    if (compensationBusy) return;
    const formElement = formEvent.currentTarget;
    const form = new FormData(formElement);
    const payload = {
      userId: Number(form.get('userId')),
      eventId: Number(form.get('eventId')),
      requestId: crypto.randomUUID(),
      incidentReference: String(form.get('incidentReference')).trim(),
      reason: String(form.get('reason')).trim(),
      moneyAmount: Number(form.get('moneyAmount')),
      pointAmount: Number(form.get('pointAmount')),
    };
    setCompensationBusy(true);
    try {
      const result = await apiRequest('/admin/compensations', actor, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      setNotice({ tone: 'success', message: `${result.message} · compensation #${result.compensationId}` });
      formElement.reset();
      await refreshData();
      await loadAdminPayouts(payoutFilters, null, false);
    } catch (error) {
      setNotice({ tone: 'error', message: `장애 보상을 처리하지 못했습니다 · ${error.message}` });
    } finally {
      setCompensationBusy(false);
    }
  }

  async function loadAdminPayouts(filters = payoutFilters, beforeId = null, append = false) {
    setPayoutSearchBusy(true);
    try {
      const params = new URLSearchParams({ limit: '20' });
      if (filters.userId) params.set('userId', filters.userId);
      if (filters.sourceType) params.set('sourceType', filters.sourceType);
      if (filters.from) params.set('from', new Date(filters.from).toISOString());
      if (filters.to) params.set('to', new Date(filters.to).toISOString());
      if (beforeId) params.set('beforeId', String(beforeId));
      const rows = await apiRequest(`/admin/payouts?${params.toString()}`, actor);
      setPayoutRows((current) => append ? [...current, ...rows] : rows);
      setPayoutCursor(rows.length ? rows[rows.length - 1].id : null);
      setPayoutHasMore(rows.length === 20);
    } catch (error) {
      setNotice({ tone: 'error', message: `지급 이력을 불러오지 못했습니다 · ${error.message}` });
    } finally {
      setPayoutSearchBusy(false);
    }
  }

  async function searchAdminPayouts(formEvent) {
    formEvent.preventDefault();
    const form = new FormData(formEvent.currentTarget);
    const filters = {
      userId: String(form.get('userId') || '').trim(),
      sourceType: String(form.get('sourceType') || ''),
      from: String(form.get('from') || ''),
      to: String(form.get('to') || ''),
    };
    setPayoutFilters(filters);
    await loadAdminPayouts(filters, null, false);
  }

  function renderEvents() {
    return (
      <>
        <section className="metric-grid" aria-label="이벤트 요약">
          <article className="metric-card metric-card--dark">
            <div className="metric-top"><span>진행 중 이벤트</span><span className="metric-index">01</span></div>
            <strong>{formatAmount(openEvents.length)}</strong>
            <span className="metric-foot">현재 OPEN 상태</span>
          </article>
          <article className="metric-card">
            <div className="metric-top"><span>지금 받을 보상</span><span className="metric-index">02</span></div>
            <strong>{formatAmount(claimableCount)}</strong>
            <span className="metric-foot">내 계정 기준</span>
          </article>
          <article className="metric-card">
            <div className="metric-top"><span>예약 지급</span><span className="metric-index">03</span></div>
            <strong>{formatAmount(batchCount)}</strong>
            <span className="metric-foot">일정 정보만 표시</span>
          </article>
        </section>

        <section className="workspace-grid">
          <div className="event-list-panel panel">
            <div className="section-heading">
              <div>
                <span className="eyebrow">EVENT CATALOG</span>
                <h2>이벤트 목록</h2>
              </div>
              <label className="filter-control">
                <span className="sr-only">이벤트 상태 필터</span>
                <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                  <option value="ALL">전체 상태</option>
                  <option value="OPEN">진행 중</option>
                  <option value="DRAFT">준비 중</option>
                  <option value="CLOSED">종료</option>
                </select>
              </label>
            </div>
            {loading && events.length === 0 ? (
              <div className="empty-state">이벤트를 불러오고 있습니다.</div>
            ) : visibleEvents.length === 0 ? (
              <div className="empty-state">표시할 이벤트가 없습니다.</div>
            ) : (
              <div className="event-list">
                {visibleEvents.map((event, index) => (
                  <button
                    className={`event-row ${event.id === selectedEventId ? 'is-selected' : ''}`}
                    key={event.id}
                    onClick={() => { setSelectedEventId(event.id); setClaimFeedback(null); }}
                    type="button"
                  >
                    <span className={`event-number ${event.mode === 'BATCH' ? 'event-number--batch' : ''}`}>
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <span className="event-row-copy">
                      <span className="event-row-title">{event.name}</span>
                      <span className="event-row-subtitle">
                        {event.mode === 'CLAIM' ? '직접 수령' : `예약 일괄 지급 · ${event.recipientCount}명 대상`} · {formatDate(event.startsAt)} 시작
                      </span>
                    </span>
                    <span className={`status-pill status-pill--${event.status.toLowerCase()}`}>
                      {statusLabel(event.status)}
                    </span>
                    <span className="event-row-arrow" aria-hidden="true">↗</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <aside className="event-detail-panel panel" aria-label="선택한 이벤트 상세">
            {selectedEvent ? (
              <>
                <div className="detail-topline">
                  <span className="eyebrow">EVENT / {String(selectedEvent.id).padStart(4, '0')}</span>
                  <span className="detail-mode">{selectedEvent.mode === 'CLAIM' ? '직접 수령' : '예약 지급'}</span>
                </div>
                <h2>{selectedEvent.name}</h2>
                <p className="detail-intro">
                  {selectedEvent.mode === 'CLAIM'
                    ? '이벤트 기간에 한 번 받을 수 있는 보상입니다.'
                    : '대상자와 예약 일정이 등록된 일괄 지급 이벤트입니다.'}
                </p>

                <div className="reward-block">
                  <span className="eyebrow">지급 보상</span>
                  <div className="reward-values">
                    <div><span className="currency-dot currency-dot--money" /><span>Money</span><strong>{formatAmount(selectedEvent.rewardMoney)}</strong></div>
                    <div><span className="currency-dot currency-dot--point" /><span>Point</span><strong>{formatAmount(selectedEvent.rewardPoint)}</strong></div>
                  </div>
                </div>

                <dl className="detail-facts">
                  <div><dt>상태</dt><dd>{statusLabel(selectedEvent.status)}</dd></div>
                  <div><dt>시작</dt><dd>{formatDate(selectedEvent.startsAt)}</dd></div>
                  <div><dt>종료</dt><dd>{formatDate(selectedEvent.endsAt)}</dd></div>
                  {selectedEvent.mode === 'BATCH' && (
                    <><div><dt>대상 사용자</dt><dd>{formatAmount(selectedEvent.recipientCount)}명</dd></div><div><dt>예약 시각</dt><dd>{formatDate(selectedEvent.scheduledAt)}</dd></div></>
                  )}
                </dl>

                {selectedEvent.mode === 'CLAIM' && (
                  <div className="claim-area">
                    {selectedEvent.claimed ? (
                      <div className="claimed-state"><span aria-hidden="true">✓</span> 이미 받은 이벤트입니다.</div>
                    ) : (
                      <button
                        className="button button--primary button--wide"
                        disabled={!getClaimAvailability(selectedEvent) || claimBusy}
                        onClick={() => handleClaim(selectedEvent)}
                        type="button"
                      >
                        {claimBusy ? '지급 결과 확인 중…' : getClaimAvailability(selectedEvent) ? '보상 받기' : '현재 받을 수 없음'}
                        {!claimBusy && <span aria-hidden="true">↗</span>}
                      </button>
                    )}
                    {claimFeedback && (
                      <p className={`inline-feedback inline-feedback--${claimFeedback.tone}`} role="status">
                        {claimFeedback.message}
                      </p>
                    )}
                    <p className="claim-note">수령 요청은 DB 기록을 기준으로 한 번만 반영됩니다.</p>
                  </div>
                )}

                {selectedEvent.mode === 'BATCH' && (
                  <div className="batch-note">
                    <span className="batch-note-mark">B</span>
                    <div><strong>예약 지급 이벤트</strong><p>대상 명단과 지급 상태는 운영 관리에서 확인하고 실행합니다.</p></div>
                  </div>
                )}
              </>
            ) : (
              <div className="empty-detail"><span className="empty-detail-mark">↗</span><h2>이벤트를 선택하세요</h2><p>목록에서 이벤트를 고르면 보상과 수령 상태를 확인할 수 있습니다.</p></div>
            )}
          </aside>
        </section>
      </>
    );
  }

  function renderWallet() {
    return (
      <section className="wallet-view">
        <div className="wallet-intro panel">
          <div>
            <span className="eyebrow">PLAYER WALLET / {userId}</span>
            <h2>{wallet?.nickname || selectedUser?.name || '사용자'}님의 보유 재화</h2>
            <p>지급 기록과 현재 잔액을 같은 데이터베이스에서 조회합니다.</p>
          </div>
          <button className="button button--quiet" onClick={() => refreshData()} type="button">새로고침 <span aria-hidden="true">↻</span></button>
        </div>
        <div className="wallet-balance-grid">
          <article className="balance-card balance-card--money">
            <div className="balance-label"><span className="currency-dot currency-dot--money" />MONEY <span>게임머니</span></div>
            <strong>{formatAmount(wallet?.moneyBalance)}</strong>
            <div className="balance-foot"><span>현재 잔액</span><span className="balance-symbol">M</span></div>
          </article>
          <article className="balance-card balance-card--point">
            <div className="balance-label"><span className="currency-dot currency-dot--point" />POINT <span>이벤트 포인트</span></div>
            <strong>{formatAmount(wallet?.pointBalance)}</strong>
            <div className="balance-foot"><span>현재 잔액</span><span className="balance-symbol">P</span></div>
          </article>
        </div>
        <div className="wallet-reset-panel panel">
          <div className="reset-panel-copy">
            <span className="eyebrow">LOCAL RESET</span>
            <h2>내 지갑 초기화</h2>
            <p>현재 선택 사용자 {userId} · {wallet?.nickname || selectedUser?.name || '데모 사용자'}의 Money와 Point를 0으로 되돌립니다. 다시 지급받을 수 있도록 이 사용자의 grant와 원장도 함께 지웁니다.</p>
          </div>
          {!myWalletResetConfirm ? (
            <button
              className="button button--danger-quiet"
              disabled={!connected || myWalletResetBusy}
              onClick={() => setMyWalletResetConfirm(true)}
              type="button"
            >내 지갑 초기화</button>
          ) : (
            <div className="reset-confirm" role="group" aria-label="내 지갑 초기화 확인">
              <p><strong>{wallet?.nickname || selectedUser?.name || userId} · 사용자 #{userId}</strong>의 모든 지급 기록과 잔액을 초기화합니다. 다른 사용자의 지갑은 바뀌지 않습니다. 이벤트·대상 명단·배치 실행 이력은 보존됩니다.</p>
              <div className="reset-confirm-actions">
                <button className="button button--danger" disabled={myWalletResetBusy} onClick={resetMyWallet} type="button">
                  {myWalletResetBusy ? '초기화 중…' : '내 지갑 초기화 확정'}
                </button>
                <button className="button button--quiet" disabled={myWalletResetBusy} onClick={() => setMyWalletResetConfirm(false)} type="button">취소</button>
              </div>
            </div>
          )}
        </div>
        <div className="panel ledger-panel">
          <div className="section-heading">
            <div><span className="eyebrow">WALLET LEDGER</span><h2>최근 지급 내역</h2></div>
            <span className="record-count">최근 {ledger.length}건</span>
          </div>
          {ledger.length === 0 ? (
            <div className="empty-state empty-state--ledger"><span className="ledger-empty-mark">—</span><strong>아직 지급 내역이 없습니다</strong><span>이벤트 보상을 받으면 원장에 기록됩니다.</span></div>
          ) : (
            <div className="table-scroll">
              <table className="ledger-table">
                <thead><tr><th>지급 내용</th><th>재화</th><th>수량</th><th>지급 시각</th></tr></thead>
                <tbody>
          {ledger.map((entry) => (
                    <tr key={entry.id}>
                      <td>
                        <span className="ledger-event-name">{entry.sourceType === 'COMPENSATION' ? `${entry.eventName} · 장애 보상` : entry.eventName}</span>
                        <span className="ledger-event-id">{entry.sourceType === 'COMPENSATION' ? `COMPENSATION #${entry.compensationId}` : `GRANT #${entry.rewardGrantId}`}</span>
                        {entry.reason && <span className="ledger-event-reason">{entry.reason}</span>}
                      </td>
                      <td><span className={`ledger-currency ledger-currency--${entry.currencyType.toLowerCase()}`}>{entry.currencyType}</span></td>
                      <td className="ledger-amount">+{formatAmount(entry.amount)}</td>
                      <td className="ledger-date">{formatDate(entry.createdAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>
    );
  }

  function renderAdmin() {
    return (
      <section className="admin-view">
        <div className="admin-intro panel">
          <div><span className="eyebrow">OPERATOR CONSOLE</span><h2>이벤트 정책을 관리합니다</h2><p>새 이벤트를 DRAFT로 등록하고 상태 전이를 실행합니다. 변경 이력은 DB에 저장됩니다.</p></div>
          <span className="admin-seal">ADMIN<br />LOCAL</span>
        </div>
        <div className="admin-reset-panel panel">
          <div className="reset-panel-copy">
            <span className="eyebrow">LOCAL DATA RESET</span>
            <h2>전체 유저 지갑 초기화</h2>
            <p>DB에 등록된 모든 사용자(실험용 사용자 포함)의 Money·Point, 지급 grant, 원장을 초기화합니다. 이벤트, BATCH 대상 명단, 실행 이력은 보존됩니다.</p>
          </div>
          {!allWalletResetConfirm ? (
            <button
              className="button button--danger-quiet"
              disabled={!connected || allWalletResetBusy}
              onClick={() => setAllWalletResetConfirm(true)}
              type="button"
            >전체 유저 초기화</button>
          ) : (
            <div className="reset-confirm reset-confirm--global" role="group" aria-label="전체 유저 초기화 확인">
              <p><strong>모든 사용자 데이터에 적용됩니다.</strong> 지급/원장 기록과 두 잔액을 0으로 초기화합니다. 사용자·이벤트와 배치 실행 이력은 남습니다. 계속하려면 아래 문구를 입력하세요.</p>
              <label className="reset-confirm-field">
                <span>확인 문구</span>
                <input
                  autoComplete="off"
                  value={allWalletResetPhrase}
                  onChange={(event) => setAllWalletResetPhrase(event.target.value)}
                  placeholder="전체 초기화"
                />
              </label>
              <div className="reset-confirm-actions">
                <button
                  className="button button--danger"
                  disabled={allWalletResetBusy || allWalletResetPhrase.trim() !== '전체 초기화'}
                  onClick={resetAllWallets}
                  type="button"
                >{allWalletResetBusy ? '전체 초기화 중…' : '전체 초기화 실행'}</button>
                <button
                  className="button button--quiet"
                  disabled={allWalletResetBusy}
                  onClick={() => { setAllWalletResetConfirm(false); setAllWalletResetPhrase(''); }}
                  type="button"
                >취소</button>
              </div>
            </div>
          )}
        </div>
        {P3_COMPENSATION_ENABLED && (
          <section className="panel compensation-panel" aria-labelledby="compensation-title">
            <div className="section-heading">
              <div><span className="eyebrow">INCIDENT COMPENSATION</span><h2 id="compensation-title">장애 보상 지급</h2></div>
              <span className="form-step">P3 · CS</span>
            </div>
            <p className="compensation-intro">영향을 받은 사용자 한 명을 선택해 Money 또는 Point를 지급합니다. 같은 이벤트·장애 식별자·사용자 조합은 한 번만 기록됩니다.</p>
            <form className="compensation-form" onSubmit={submitCompensation}>
              <label className="field"><span>대상 사용자</span><select name="userId" defaultValue="101" required>
                {USERS.map((user) => <option key={user.id} value={user.id}>{user.id} · {user.name}</option>)}
              </select></label>
              <label className="field"><span>관련 이벤트</span><select name="eventId" defaultValue={events[0]?.id ?? ''} required>
                {events.map((event) => <option key={event.id} value={event.id}>#{event.id} · {event.name}</option>)}
              </select></label>
              <label className="field"><span>장애 식별자</span><input name="incidentReference" maxLength="100" placeholder="예: OUTAGE-P3-001" required /></label>
              <label className="field field--full"><span>보상 사유</span><textarea name="reason" maxLength="500" placeholder="장애 영향과 지급 판단 근거를 적어 주세요." rows="3" required /></label>
              <div className="form-row field--full compensation-amounts">
                <label className="field"><span>Money</span><input name="moneyAmount" type="number" min="0" step="1" defaultValue="0" required /></label>
                <label className="field"><span>Point</span><input name="pointAmount" type="number" min="0" step="1" defaultValue="0" required /></label>
              </div>
              <div className="compensation-submit field--full">
                <button className="button button--primary" disabled={compensationBusy || events.length === 0} type="submit">
                  {compensationBusy ? '보상 기록 중…' : '장애 보상 지급'} <span aria-hidden="true">↗</span>
                </button>
                <p className="form-footnote">요청 ID는 전송 때 자동 생성됩니다. 같은 장애를 같은 사용자에게 재등록하면 DB가 추가 지급을 막습니다.</p>
              </div>
            </form>
            <div className="payout-search">
              <div className="section-heading payout-search-heading">
                <div><span className="eyebrow">PAYOUT HISTORY</span><h3>지급 이력 검색</h3></div>
                <span className="record-count">{formatAmount(payoutRows.length)}건 표시</span>
              </div>
              <form className="payout-search-form" onSubmit={searchAdminPayouts}>
                <label className="field"><span>사용자 ID</span><input name="userId" type="number" min="1" placeholder="전체 사용자" defaultValue={payoutFilters.userId} /></label>
                <label className="field"><span>출처</span><select name="sourceType" defaultValue={payoutFilters.sourceType}><option value="">전체</option><option value="EVENT">이벤트 지급</option><option value="COMPENSATION">장애 보상</option></select></label>
                <label className="field"><span>시작 시각</span><input name="from" type="datetime-local" defaultValue={payoutFilters.from} /></label>
                <label className="field"><span>종료 시각</span><input name="to" type="datetime-local" defaultValue={payoutFilters.to} /></label>
                <button className="button button--quiet payout-search-submit" disabled={payoutSearchBusy} type="submit">{payoutSearchBusy ? '검색 중…' : '검색'}</button>
              </form>
              {payoutRows.length === 0 ? (
                <div className="empty-state payout-empty">검색 결과가 없습니다. 검색 조건을 적용해 확인하세요.</div>
              ) : (
                <div className="table-scroll">
                  <table className="ledger-table payout-table">
                    <thead><tr><th>지급 내용</th><th>사용자</th><th>재화</th><th>수량</th><th>지급 시각</th></tr></thead>
                    <tbody>{payoutRows.map((entry) => (
                      <tr key={entry.id}>
                        <td>
                          <span className="ledger-event-name">{entry.eventName}{entry.sourceType === 'COMPENSATION' ? ' · 장애 보상' : ''}</span>
                          <span className="ledger-event-id">{entry.sourceType === 'COMPENSATION' ? `COMPENSATION #${entry.compensationId}` : `GRANT #${entry.rewardGrantId}`}</span>
                          {entry.reason && <span className="ledger-event-reason">{entry.reason}</span>}
                        </td>
                        <td>#{entry.userId}</td>
                        <td><span className={`ledger-currency ledger-currency--${entry.currencyType.toLowerCase()}`}>{entry.currencyType}</span></td>
                        <td className="ledger-amount">+{formatAmount(entry.amount)}</td>
                        <td className="ledger-date">{formatDate(entry.createdAt)}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
              {payoutHasMore && <button className="button button--quiet button--small payout-load-more" disabled={payoutSearchBusy} onClick={() => loadAdminPayouts(payoutFilters, payoutCursor, true)} type="button">이전 지급 더 보기</button>}
            </div>
          </section>
        )}
        <div className="admin-grid">
          <form className="panel event-form" onSubmit={createEvent}>
            <div className="section-heading"><div><span className="eyebrow">NEW EVENT</span><h2>이벤트 등록</h2></div><span className="form-step">01 / 02</span></div>
            <label className="field field--full"><span>이벤트 이름</span><input name="name" maxLength="100" placeholder="예: 겨울 출석 보상" required /></label>
            <label className="field"><span>지급 방식</span><select name="mode" value={formMode} onChange={(event) => setFormMode(event.target.value)}><option value="CLAIM">직접 수령 · CLAIM</option><option value="BATCH">예약 지급 · BATCH</option></select></label>
            <div className="form-row">
              <label className="field"><span>시작 시각</span><input name="startsAt" type="datetime-local" defaultValue={localInputDate(0, 9)} required /></label>
              <label className="field"><span>종료 시각</span><input name="endsAt" type="datetime-local" defaultValue={localInputDate(7, 23)} required /></label>
            </div>
            {formMode === 'BATCH' && <label className="field field--full field--schedule"><span>예약 지급 시각</span><input name="scheduledAt" type="datetime-local" defaultValue={localInputDate(1, 9)} required /></label>}
            <div className="form-row reward-inputs">
              <label className="field"><span>Money 보상</span><input name="rewardMoney" type="number" min="0" step="1" defaultValue="1000" required /></label>
              <label className="field"><span>Point 보상</span><input name="rewardPoint" type="number" min="0" step="1" defaultValue="200" required /></label>
            </div>
            <button className="button button--primary button--wide" disabled={formBusy} type="submit">
              {formBusy ? '등록 중…' : 'DRAFT로 등록'} <span aria-hidden="true">↗</span>
            </button>
            <p className="form-footnote">둘 중 한 보상은 0보다 커야 합니다. OPEN 이후에는 이 화면에서 보상액을 바꾸지 않습니다.</p>
          </form>

          <div className="panel admin-events-panel">
            <div className="section-heading"><div><span className="eyebrow">EVENT LIFECYCLE</span><h2>상태 전이</h2></div><span className="form-step">02 / 02</span></div>
            <div className="admin-event-list">
              {events.length === 0 ? <div className="empty-state">등록된 이벤트가 없습니다.</div> : events.map((event) => {
                const distribution = batchDistributions[event.id];
                const isBusy = Boolean(batchBusy[event.id]);
                const scheduleReached = event.scheduledAt && Date.now() >= new Date(event.scheduledAt).getTime();
                return (
                  <article className="admin-event-card" key={event.id}>
                    <div className="admin-event-row">
                      <div className="admin-event-meta"><span className={`status-marker status-marker--${event.status.toLowerCase()}`} /><div><strong>{event.name}</strong><span>#{event.id} · {event.mode} · {statusLabel(event.status)}</span></div></div>
                      <div className="admin-event-actions">
                        {event.status === 'DRAFT' && <button className="button button--small button--primary" onClick={() => changeStatus(event, 'OPEN')} type="button">오픈</button>}
                        {event.status === 'OPEN' && <button className="button button--small button--outline" onClick={() => changeStatus(event, 'CLOSED')} type="button">종료</button>}
                        {event.status === 'CLOSED' && <span className="closed-label">완료</span>}
                      </div>
                    </div>
                    {event.mode === 'BATCH' && (
                      <div className="batch-admin-panel">
                        <div className="batch-admin-summary">
                          <span>대상 <strong>{formatAmount(distribution?.recipientCount ?? event.recipientCount)}</strong></span>
                          <span>지급 <strong>{formatAmount(distribution?.grantedCount)}</strong></span>
                          <span>미지급 <strong>{formatAmount(distribution?.pendingCount ?? event.recipientCount)}</strong></span>
                        </div>
                        {event.status === 'DRAFT' && (
                          <form className="recipient-form" onSubmit={(formEvent) => replaceBatchRecipients(formEvent, event.id)}>
                            <label className="field"><span>대상 사용자 ID</span><textarea
                              name="userIds"
                              value={batchRecipientText[event.id] ?? ''}
                              onChange={(inputEvent) => setBatchRecipientText((current) => ({ ...current, [event.id]: inputEvent.target.value }))}
                              placeholder="101, 102, 103"
                              rows="3"
                            /></label>
                            <button className="button button--quiet button--small" disabled={isBusy} type="submit">{isBusy ? '저장 중…' : '명단 교체'}</button>
                            <p className="form-footnote">쉼표나 줄바꿈으로 구분합니다. 저장하면 기존 명단을 교체하며 OPEN 뒤에는 바꿀 수 없습니다.</p>
                          </form>
                        )}
                        {event.status === 'OPEN' && (
                          <div className="batch-run-controls">
                            <span className="batch-schedule">예약 {formatDate(event.scheduledAt)}</span>
                            <button className="button button--small button--primary" disabled={isBusy || !scheduleReached} onClick={() => runBatch(event.id)} type="button">
                              {isBusy ? '지급 처리 중…' : scheduleReached ? '미지급자 지급' : '예약 시각 대기'}
                            </button>
                          </div>
                        )}
                        {distribution?.recentRuns?.[0] && (
                          <div className="batch-last-run">
                            <span>최근 실행 · {distribution.recentRuns[0].status}</span>
                            <span>{formatAmount(distribution.recentRuns[0].paidCount)}명 지급 / {formatAmount(distribution.recentRuns[0].failedCount)}명 오류</span>
                            {distribution.recentRuns[0].errorSummary && <small>{distribution.recentRuns[0].errorSummary}</small>}
                          </div>
                        )}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
            <div className="transition-rule"><span>허용된 흐름</span><strong>DRAFT <i>→</i> OPEN <i>→</i> CLOSED</strong><small>종료된 이벤트는 다시 열 수 없습니다.</small></div>
          </div>
        </div>
      </section>
    );
  }

  const navItems = NAV_ITEMS.filter((item) => !item.admin || role === 'ADMIN');

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#main" onClick={() => setView('events')}>
          <span className="brand-mark"><i /><i /><i /></span>
          <span className="brand-copy"><strong>REWARD</strong><small>OPERATIONS</small></span>
        </a>
        <div className="sidebar-label">WORKSPACE</div>
        <nav className="primary-nav" aria-label="주 메뉴">
          {navItems.map((item) => (
            <button className={`nav-item ${view === item.id ? 'is-active' : ''}`} key={item.id} onClick={() => setView(item.id)} type="button">
              <span className="nav-mark">{item.mark}</span><span>{item.label}</span>{item.admin && <span className="nav-admin">ADMIN</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-status"><span className={`connection-dot ${connected ? 'is-on' : ''}`} /><div><strong>{connected ? '데이터베이스 연결' : '연결 대기 중'}</strong><small>PostgreSQL · local</small></div></div>
          <div className="sidebar-build"><span>REWARD DB LAB</span><span>PHASE 01—02</span></div>
        </div>
      </aside>

      <main id="main" className="main-area">
        <header className="topbar">
          <div className="breadcrumb"><span>REWARD OPS</span><span className="breadcrumb-separator">/</span><strong>{pageTitle}</strong></div>
          <div className="topbar-controls">
            <span className="demo-label"><span className="demo-dot" /> 로컬 데모</span>
            <label className="actor-select"><span className="sr-only">데모 역할 선택</span><select value={role} onChange={(event) => setRole(event.target.value)}><option value="USER">사용자</option><option value="ADMIN">운영자</option></select></label>
            <label className="user-select"><span className="sr-only">데모 사용자 선택</span><select value={userId} onChange={(event) => setUserId(Number(event.target.value))}>{USERS.map((user) => <option key={user.id} value={user.id}>{user.id} · {user.name}</option>)}</select></label>
            <span className="avatar" aria-label={`${userId}번 사용자`}>{selectedUser?.name?.[0] || 'U'}</span>
          </div>
        </header>

        <div className="page-content">
          <div className="page-heading">
            <div><span className="eyebrow">REWARD WORKSPACE <span>·</span> 2026</span><h1>{pageTitle}</h1><p>{view === 'events' ? '이벤트 상태와 내 수령 가능 보상을 확인합니다.' : view === 'wallet' ? '지급으로 변한 잔액과 원장을 확인합니다.' : '이벤트 등록과 상태 변경을 관리합니다.'}</p></div>
            <div className="heading-actions">
              <span className={`api-status ${connected ? 'is-connected' : ''}`}><span />{connected ? 'API 연결됨' : 'API 확인 필요'}</span>
              <button className="icon-button" onClick={() => refreshData()} type="button" aria-label="데이터 새로고침">↻</button>
            </div>
          </div>

          {pageError && <div className="page-alert" role="alert"><span className="alert-mark">!</span><div><strong>서버에서 데이터를 불러오지 못했습니다.</strong><p>{pageError}</p><span>DB와 API를 실행한 뒤 다시 불러와 주세요.</span></div><button onClick={() => refreshData()} type="button">다시 시도</button></div>}
          {view === 'events' && renderEvents()}
          {view === 'wallet' && renderWallet()}
          {view === 'admin' && role === 'ADMIN' && renderAdmin()}
          <footer className="page-footer"><span>REWARD DB LAB</span><span>사용자 지급은 데이터베이스 트랜잭션으로 처리됩니다.</span><span>LOCAL DEMO</span></footer>
        </div>
      </main>

      {notice && <div className={`toast toast--${notice.tone}`} role="status"><span className="toast-mark">{notice.tone === 'success' ? '✓' : '!'}</span>{notice.message}<button onClick={() => setNotice(null)} type="button" aria-label="알림 닫기">×</button></div>}
    </div>
  );
}

export default App;
