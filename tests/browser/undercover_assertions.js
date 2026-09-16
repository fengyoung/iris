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
    _players = [{key:'base_model/a', model_id:'a', alive:true}];
    renderPlayerStrip();
    check(document.getElementById('player-strip').textContent.includes('P1'), '玩家稳定编号');
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
    document.body.dataset.testResult = 'PASS';
    document.body.dataset.tests = results.join('；');
  } catch(e) {
    document.body.dataset.testResult = 'FAIL: ' + e.message;
  }
})();
