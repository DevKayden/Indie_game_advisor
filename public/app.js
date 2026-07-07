/* ──────────────────────────────────────────────────────────────────────────
   GameDev AI Advisor — Frontend Application Logic
   ────────────────────────────────────────────────────────────────────────── */

// ── 상태 변수 ─────────────────────────────────────────────────────────────────
let sessionId = generateUUID();
let isProcessing = false;
let currentEventSource = null;

// ── DOM 참조 ──────────────────────────────────────────────────────────────────
const chatMessages   = document.getElementById('chat-messages');
const chatForm       = document.getElementById('chat-form');
const messageInput   = document.getElementById('message-input');
const sendBtn        = document.getElementById('send-btn');
const consoleLog     = document.getElementById('console-log');
const statusBadge    = document.getElementById('status-badge');
const statusText     = document.getElementById('status-text');
const statusDot      = statusBadge.querySelector('.status-dot');
const newChatBtn     = document.getElementById('new-chat-btn');
const clearConsoleBtn = document.getElementById('clear-console-btn');
const gameCardsPanel = document.getElementById('game-cards-panel');
const gameCardsList  = document.getElementById('game-cards-list');

// ── 유틸리티 ─────────────────────────────────────────────────────────────────

function generateUUID() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

function setStatus(type) {
  statusDot.className = 'status-dot';
  if (type === 'running') {
    statusDot.classList.add('running');
    statusText.textContent = '분석 중';
  } else if (type === 'error') {
    statusDot.classList.add('error');
    statusText.textContent = '오류';
  } else {
    statusText.textContent = '대기 중';
  }
}

// ── 마크다운 파서 (경량 구현) ─────────────────────────────────────────────────

function parseMarkdown(text) {
  let html = text
    // 코드 블록 (멀티라인) — 먼저 처리
    .replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) =>
      `<pre><code class="lang-${lang}">${escapeHtml(code.trim())}</code></pre>`)
    // 인라인 코드
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    // 헤딩
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    // 굵게 + 이탤릭
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // 수평선
    .replace(/^---+$/gm, '<hr/>')
    // 순서 없는 목록
    .replace(/^[\*\-] (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
    // 순서 있는 목록
    .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
    // 링크
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    // 단락
    .replace(/\n{2,}/g, '</p><p>')
    // 줄바꿈
    .replace(/\n/g, '<br/>');

  // 단락으로 감싸기
  if (!html.startsWith('<h') && !html.startsWith('<ul') && !html.startsWith('<pre')) {
    html = `<p>${html}</p>`;
  }
  // ul 태그 병합 처리
  html = html.replace(/<\/ul>\s*<ul>/g, '');

  return html;
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── 콘솔 로그 ─────────────────────────────────────────────────────────────────

const TYPE_CLASS_MAP = {
  safety:     'cl-safety',
  agent:      'cl-agent',
  tool_start: 'cl-tool-start',
  tool_end:   'cl-tool-end',
  finish:     'cl-finish',
  error:      'cl-error',
  log:        'cl-log',
};

function addConsoleLog(message, type = 'log') {
  const cls = TYPE_CLASS_MAP[type] || 'cl-log';
  const line = document.createElement('div');
  line.className = `console-line ${cls}`;
  line.textContent = `> ${message}`;
  consoleLog.appendChild(line);
  consoleLog.scrollTop = consoleLog.scrollHeight;
}

function clearConsole() {
  consoleLog.innerHTML = '<div class="console-line cl-system">&gt; 콘솔 초기화됨...</div>';
}

// ── 채팅 메시지 추가 ──────────────────────────────────────────────────────────

function addUserMessage(text) {
  const div = document.createElement('div');
  div.className = 'message msg-user';
  div.innerHTML = `
    <div class="msg-avatar">나</div>
    <div class="msg-body">
      <div class="msg-bubble"><p>${escapeHtml(text)}</p></div>
    </div>`;
  chatMessages.appendChild(div);
  scrollChat();
  return div;
}

function addAIMessageLoading() {
  const div = document.createElement('div');
  div.className = 'message msg-ai';
  div.id = 'ai-loading-msg';
  div.innerHTML = `
    <div class="msg-avatar">AI</div>
    <div class="msg-body">
      <div class="msg-bubble">
        <div class="loading-dots">
          <span></span><span></span><span></span>
        </div>
      </div>
    </div>`;
  chatMessages.appendChild(div);
  scrollChat();
  return div;
}

function replaceLoadingWithResponse(text) {
  const loading = document.getElementById('ai-loading-msg');
  if (!loading) return;
  const bubble = loading.querySelector('.msg-bubble');
  bubble.innerHTML = parseMarkdown(text);
  loading.removeAttribute('id');
}

function addAIErrorMessage(text) {
  const loading = document.getElementById('ai-loading-msg');
  if (loading) {
    const bubble = loading.querySelector('.msg-bubble');
    bubble.innerHTML = `<p style="color:var(--accent-red)">${escapeHtml(text)}</p>`;
    loading.removeAttribute('id');
  }
}

function scrollChat() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

// ── 게임 카드 렌더링 ──────────────────────────────────────────────────────────

function renderGameCards(marketData) {
  if (!marketData || marketData.length === 0) {
    gameCardsPanel.style.display = 'none';
    return;
  }

  gameCardsList.innerHTML = '';
  marketData.forEach(game => {
    const videos = game.youtube_videos || [];
    const card = document.createElement('div');
    card.className = 'game-card';
    card.innerHTML = `
      <div class="game-card-header">
        ${game.img ? `<img class="game-card-img" src="${game.img}" alt="${escapeHtml(game.name)}" onerror="this.style.display='none'">` : ''}
        <span class="game-card-title">${escapeHtml(game.name)}</span>
      </div>
      <div class="game-card-stats">
        <span class="stat-badge stat-players">동접자 ${(game.players || 0).toLocaleString()}명</span>
        <span class="stat-badge stat-price">${escapeHtml(game.price || 'N/A')}</span>
      </div>
      ${videos.length > 0 ? `
      <div class="game-card-videos">
        <div class="video-label">리뷰 영상</div>
        ${videos.map(v => `<a class="video-link" href="${v.url}" target="_blank" rel="noopener">${escapeHtml(v.title)}</a>`).join('')}
      </div>` : ''}`;
    gameCardsList.appendChild(card);
  });

  gameCardsPanel.style.display = 'block';
}

// ── 메시지 전송 ───────────────────────────────────────────────────────────────

async function sendMessage(text) {
  if (isProcessing || !text.trim()) return;

  isProcessing = true;
  sendBtn.disabled = true;
  messageInput.disabled = true;
  setStatus('running');
  addConsoleLog('새 요청 전송 중...', 'system');

  addUserMessage(text);
  addAIMessageLoading();

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });

    if (!resp.ok) throw new Error(`서버 오류: ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // 미완성 라인은 버퍼에 유지

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const rawJson = line.slice(6).trim();
        if (!rawJson) continue;

        let payload;
        try { payload = JSON.parse(rawJson); } catch { continue; }

        const { type, message, response, market_data, rag_results, session_id: sid } = payload;

        if (sid) sessionId = sid;

        if (type === 'result') {
          replaceLoadingWithResponse(response || '(응답 없음)');
          renderGameCards(market_data);
          addConsoleLog('에이전트 실행 완료.', 'finish');
          setStatus('idle');

        } else if (type === 'error') {
          addAIErrorMessage(message || '알 수 없는 오류');
          addConsoleLog(message || '오류 발생', 'error');
          setStatus('error');

        } else {
          // 로그 메시지
          addConsoleLog(message || '', type || 'log');
        }
      }
    }

  } catch (err) {
    addAIErrorMessage(`연결 오류: ${err.message}`);
    addConsoleLog(`연결 오류: ${err.message}`, 'error');
    setStatus('error');
  } finally {
    isProcessing = false;
    sendBtn.disabled = false;
    messageInput.disabled = false;
    messageInput.focus();
    autoResize();
  }
}

// ── 자동 리사이즈 텍스트영역 ─────────────────────────────────────────────────

function autoResize() {
  messageInput.style.height = 'auto';
  messageInput.style.height = Math.min(messageInput.scrollHeight, 160) + 'px';
  sendBtn.disabled = messageInput.value.trim().length === 0 || isProcessing;
}

// ── 이벤트 바인딩 ─────────────────────────────────────────────────────────────

chatForm.addEventListener('submit', e => {
  e.preventDefault();
  const text = messageInput.value.trim();
  if (!text || isProcessing) return;
  messageInput.value = '';
  autoResize();
  sendMessage(text);
});

messageInput.addEventListener('input', autoResize);

messageInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    chatForm.dispatchEvent(new Event('submit'));
  }
});

newChatBtn.addEventListener('click', () => {
  if (isProcessing) return;
  sessionId = generateUUID();
  chatMessages.innerHTML = '';
  clearConsole();
  gameCardsPanel.style.display = 'none';
  gameCardsList.innerHTML = '';
  setStatus('idle');

  // 웰컴 메시지 복원
  const welcome = document.createElement('div');
  welcome.className = 'message msg-ai';
  welcome.innerHTML = `
    <div class="msg-avatar">AI</div>
    <div class="msg-body">
      <div class="msg-bubble">
        <p>새 대화가 시작되었습니다. 기획 중인 게임에 대해 이야기해 주세요.</p>
        <div class="capability-chips">
          <span class="chip chip-market">게임 시장 조사</span>
          <span class="chip chip-design">게임 디자인 패턴 &amp; 성공 사례</span>
          <span class="chip chip-tech">기술 스택 &amp; 개발 환경 추천</span>
        </div>
      </div>
    </div>`;
  chatMessages.appendChild(welcome);
  messageInput.focus();
});

clearConsoleBtn.addEventListener('click', clearConsole);

// 초기화
autoResize();
messageInput.focus();
