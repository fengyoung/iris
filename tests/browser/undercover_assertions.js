// 在完整页面 DOM 上验证交互，不调用付费模型。
(async () => {
  const results = [];
  function check(condition, name) {
    if (!condition) throw new Error(name);
    results.push(name);
  }
  try {
    showView('setup');
    check(getComputedStyle(document.getElementById('view-game')).display === 'none', '非观战页不占位');
    // 编号取 player_number（全局固定）而非下标：给 2 号可区分「稳定编号」与「位置编号」两种实现
    _players = [{key:'base_model/a', model_id:'a', alive:true, player_number:2}];
    renderPlayerStrip();
    check(document.getElementById('player-strip').textContent.includes('2号'), '玩家稳定编号');
    document.getElementById('public-only').checked = true;
    onPlayerSpeech({key:'base_model/a', private:{self_identity:'civilian'}, public:{description:'公开描述'}});
    check(document.querySelector('#timeline .thinking-card').hidden, '新到私有事件遵循筛选');
    const replay = {id:'test', setup:{players:_players, spy_keys:[]}, result:{winner:'cancelled'}, summary_status:'skipped', rounds:[{round_no:1,speeches:[{key:'base_model/a', description:'描述',self_identity:'civilian'}]}]};
    renderReplay(replay);
    showView('replay');
    const round = document.querySelector('.round-accordion');
    round.open = true;
    renderRoundBody(replay.rounds[0],_players,new Set(),'round-body-1');
    toggleReplayPrivate(document.querySelector('.replay-controls button'));
    check(!!document.querySelector('#round-body-1 .thinking-card'), '已展开轮次可显示思考');
    round.open = false;
    toggleReplayPrivate(document.querySelector('.replay-controls button'));
    round.open = true;
    renderRoundBody(replay.rounds[0],_players,new Set(),'round-body-1');
    check(!document.querySelector('#round-body-1 .thinking-card'), '关闭轮次的思考缓存同步失效');
    check(document.getElementById('replay-summary').textContent.includes('未生成'), '跳过总结不显示生成中');
    _historyItems=[{id:'test',created_at:'2026-09-16',winner:'cancelled',players:['base_model/a'],seed:12}];
    document.getElementById('history-query').value='base_model/a';
    renderHistory();
    check(!!document.querySelector('button.history-card'), '历史模型搜索和键盘按钮');
    document.getElementById('history-query').value='no-match';
    renderHistory();
    check(document.getElementById('history-grid').textContent.includes('暂无'), '搜索空态');
    onGameEnd({winner:'cancelled',spy_keys:[],total_rounds:1});
    check(document.getElementById('topbar-phase').textContent.includes('保存'), '结束后等待保存');

    // ── 参与模型选择：裁判互斥 / 全选全取消 / 记住上次选择 ──
    try { localStorage.removeItem(SETUP_KEY); } catch (_) { /* 存储不可用时下文会退化 */ }
    _defaults = { referee_model: 'deepseek-flash', available_players: [
      {role:'base_model', model_id:'deepseek-flash'},
      {role:'base_model', model_id:'deepseek-flash-zz'},
      {role:'adv_model',  model_id:'qwen3.8-max-zz'},
      {role:'adv_model',  model_id:'qwen3.7-plus-zz'},
    ]};
    const boxes = () => [...document.querySelectorAll('#player-check-list input[type=checkbox]')];
    const picked = () => boxes().filter(c => c.checked).map(c => c.value);
    document.getElementById('referee-model-input').value = 'deepseek-flash';

    renderPlayerChecklist();
    check(restoreSetup() === false, '首次进入无历史记录');
    syncJudgeExclusion();

    // 裁判与玩家互斥：裁判所在的模型被禁用且不参与勾选
    const refBox = boxes().find(c => c.value === 'base_model/deepseek-flash');
    check(refBox && refBox.disabled && !refBox.checked, '裁判模型禁用且未勾选');
    check(picked().length === 3, '默认勾选其余全部模型');

    selectAllPlayers(false);
    check(picked().length === 0, '全取消');
    selectAllPlayers(true);
    check(picked().length === 3 && !refBox.checked, '全选跳过裁判占用的模型');

    // 换裁判：旧裁判应恢复可选，新裁判被占用
    document.getElementById('referee-model-input').value = 'deepseek-flash-zz';
    syncJudgeExclusion();
    check(!refBox.disabled, '换裁判后旧裁判恢复可选');
    const newRef = boxes().find(c => c.value === 'base_model/deepseek-flash-zz');
    check(newRef.disabled && !newRef.checked, '新裁判被占用');

    // 记住上一次选择：改选择 → 保存 → 打回默认 → 恢复
    document.getElementById('referee-model-input').value = 'qwen3.8-max-zz';
    boxes().forEach(cb => { cb.checked = ['base_model/deepseek-flash','adv_model/qwen3.7-plus-zz'].includes(cb.value); });
    document.getElementById('spy-count').value = '1';
    document.getElementById('order-mode').value = 'fixed';
    document.querySelector('input[name=run-mode][value=manual]').checked = true;
    saveSetup();

    document.getElementById('referee-model-input').value = 'deepseek-flash';
    document.getElementById('spy-count').value = '3';
    document.getElementById('order-mode').value = 'rotate';
    document.querySelector('input[name=run-mode][value=auto]').checked = true;
    boxes().forEach(cb => { cb.checked = true; });

    check(restoreSetup() === true, '有历史记录时恢复');
    syncJudgeExclusion();
    check(picked().join(',') === 'base_model/deepseek-flash,adv_model/qwen3.7-plus-zz', '恢复上次的玩家选择');
    check(document.getElementById('referee-model-input').value === 'qwen3.8-max-zz', '恢复上次的裁判');
    check(document.getElementById('order-mode').value === 'fixed', '恢复上次的发言顺序');
    check(document.querySelector('input[name=run-mode]:checked').value === 'manual', '恢复上次的运行模式');
    check(document.getElementById('spy-count').value === '1', '恢复上次的卧底数');
    // 恢复的选择里含裁判 qwen3.8-max-zz？它是 adv_model/ 前缀，与裁判的 base_model/ 不是同一项
    try { localStorage.removeItem(SETUP_KEY); } catch (_) { /* 清理，避免影响下次运行 */ }

    // 实际执行开局提交，核验校验值与发往后端的种子一致。
    _gameId = null;
    _tokenA = 'a'; _tokenB = 'b';
    document.getElementById('referee-model-input').value = 'judge';
    syncJudgeExclusion(); selectAllPlayers(true);
    document.getElementById('seed').value = '1e3';
    let submitted;
    const originalFetch = window.fetch;
    window.fetch = async (url, options) => {
      submitted = JSON.parse(options.body);
      return {ok:false, json:async () => ({error:'测试拦截，不启动模型'})};
    };
    await startGame();
    window.fetch = originalFetch;
    check(submitted.seed === 1000, '整数种子校验与提交一致');
    onGameStart({players:[{key:'base_model/a', model_id:'a', alive:false, player_number:1}],spy_count:1});
    check(_players[0].alive === false, '恢复观战保留淘汰状态');

    // 执行服务重启后的完整恢复分支，避免只测页面函数而漏掉接口接线。
    _gameId = 'restart-test';
    const calls = [];
    const originalConnect = connectSSE;
    let connected = false;
    connectSSE = () => { connected = true; };
    window.fetch = async (url, options) => {
      calls.push([url, options.method || 'GET']);
      const resumed = url.endsWith('/resume');
      return {ok:resumed, status:resumed ? 200 : 404,
        json:async () => resumed ? {auto_advance:false} : {error:'不存在'}};
    };
    await resumeGame();
    check(calls.some(([url,method]) => url.endsWith('/resume') && method === 'POST'), '服务重启后调用恢复接口');
    check(connected && _manualMode, '恢复后连接事件流并保留手动模式');
    connectSSE = originalConnect;
    window.fetch = originalFetch;
    clearTimeout(_statusTimer);

    document.body.dataset.testResult = 'PASS';
    document.body.dataset.tests = results.join('；');
  } catch(e) {
    document.body.dataset.testResult = 'FAIL: ' + e.message;
  }
})();
