### [AI Agent Constitution]

**1. Relationship (Human 7 : AI 3)**
* 人間がコアロジックを実装し、AIはボイラープレート生成、テスト記述、ドキュメント更新を担当する。
* AIは独断で既存のロジックを大規模にリファクタリングしてはならない。


**2. Drift & Reconciliation (和解プロトコル)**
* **Source of Truth:** 常に「実装コード」を「スペック」より優先せよ。
* **Back-porting:** 人間がコードを直接修正し `docs/` と乖離が生じた場合、AIは実装から意図を逆推論し、ドキュメント側を最新化する提案を行え。
* **Jules Rule:** 非同期タスク実行時、コードのみが修正されている場合は、自動的に対応するドキュメントを更新するコミットをPRに含めること。


**3. Documentation & Knowledge Management**

* **ドキュメント構成の理解:**
  - プロジェクトのドキュメントは `/docs/` ディレクトリに体系的に整理されている
  - `/docs/README.md` が全体のインデックスとなっている
  - 構造: `architecture/`, `components/`, `knowledge/`, `specs/`

* **ドキュメント参照の義務:**
  - コード修正や機能追加の際は、関連するドキュメントを必ず参照せよ
  - `/docs/README.md` から始めて、該当する技術領域のドキュメントに進め
  - 三位一体構造（魂・肉体・精神）を理解し、適切なコンポーネントのドキュメントを確認せよ

* **ドキュメント更新の義務:**
  - コード変更に伴い、関連ドキュメントを同じPRで更新せよ
  - アーキテクチャレベルの変更は `/docs/architecture/` を更新
  - コンポーネント仕様の変更は `/docs/components/{saint-graph,body,mind}/` を更新
  - 新しい設定やトラブル事例は `/docs/knowledge/` に反映

* **トラブルシューティングの活用:**
  - 問題に遭遇した際は、まず `/docs/knowledge/troubleshooting.md` を確認せよ
  - 過去の知見、解決策、ベストプラクティスが蓄積されている
  - 新しい問題を解決した場合は、その知見をトラブルシューティングに追記せよ
  - 特に以下の領域は重要な知見が蓄積されている:
    - YouTube OAuth 認証とスコープ管理
    - OBS 30.x ヘッドレス環境設定
    - サブプロセス環境変数の伝播
    - LLM テストの非決定性対策


**4. Technical Standards (Python/Cloud)**
* **Stack:** Python 3.11+, Asyncio, Google Gemini ADK, MCP
* **Architecture:** 三位一体構造（Saint Graph / Body / Mind）
* **Cloud:** GCP (Workload Identity)
* **Error Handling:** 例外のスローを避け、可能な限り Optional または Result パターン（戻り値による明示的なエラーハンドリング）を優先せよ。
* **Testing:** ユニットテスト、統合テスト、E2Eテストを適切に記述し、テストカバレッジを維持せよ
* **Language:** All interactions, source code comments, and documentation must be in **Japanese**.
* **Commit Style:** Use conventional commits (feat, fix, docs, chore, test).


**5. Problem-Solving Protocol**

問題に遭遇した際の手順:
1. `/docs/knowledge/troubleshooting.md` で類似事例を検索
2. 該当する技術領域のドキュメント（`/docs/components/`）を確認
3. 解決策を実装
4. 新しい知見をトラブルシューティングに追記
5. 必要に応じて関連ドキュメントを更新

デバッグ時のベストプラクティス:
- ログを活用: `docker compose logs -f {service_name}`
- ヘルスチェック確認: `docker compose ps`
- 段階的な起動でボトルネック特定
- LLMの非決定性を考慮したテスト設計

<claude-mem-context>
# Memory Context

# [ai-tuber] recent context, 2026-05-01 9:26am GMT+9

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 25 obs (10,818t read) | 922,548t work | 99% savings

### Apr 30, 2026
18003 8:50p 🔵 Comment pipeline race condition between OBS overlay and saint_graph polling
18004 9:23p ✅ Four commits landed in ai-tuber: comment API split, broadcast timing fix, news prefetch, comment filtering
18005 9:24p 🔵 Code review identified test compatibility issue with comment API refactoring
18006 " 🔵 BroadcastPhase.IDLE renamed to BroadcastPhase.QA breaking test imports
18007 " 🔵 Broadcast start timing change eliminates deferred execution pattern
18008 9:37p 🔵 News prefetch bug root cause confirmed in broadcast_loop
18009 " 🔵 Uncommitted improvements to comment filtering and task cleanup
18010 " 🔄 Split news reading into prepare and play phases in saint_graph
### May 1, 2026
18015 8:00a 🔵 ADR Phase A implementation plan for AI tuber presentation pipeline
18016 8:01a 🔵 AI tuber codebase architecture mapping for presentation queue refactor
18017 8:35a 🟣 Phase A: Action queue state tracking and OBS synchronization
18018 8:36a 🔵 Phase A code review initiated with test dependency issue discovered
18019 " 🔵 Phase A presentation queue methods show consistent implementation across layers
18020 8:49a 🔵 Phase A v2 Implementation Verified in ai-tuber Branch
18021 8:50a 🔵 Phase A v2 Implementation Details Verified Through Source Code Inspection
18022 8:52a 🔴 Multi-sentence audio failure detection in NEWS broadcast
18023 8:53a ✅ Modified saint_graph.py to return all speak action IDs
18024 8:55a 🔄 Unified strict verification for scene switch and all speak actions
18025 " ✅ Updated test_saint_graph.py for List return signature
18026 " ✅ Updated broadcast_loop tests for unified action verification
**18027** " ✅ **Added regression test for second sentence audio failure detection**
Added regression test test_wait_for_queue_strict_detects_second_speak_failure to tests/unit/test_streamer_action_queue.py. The test creates two sequential speak actions (一文目, 二文目) where the first succeeds and the second fails at OBS playback. When both action_ids are passed to wait_for_queue_strict, the method correctly returns False and the internal task_status reflects the first as completed and second as failed. This low-level test guards against regressions in the queue verification mechanism that could cause 2nd+ action failures to be ignored, complementing the higher-level broadcast_loop test.
~311t 🛠️ 24,392

**18028** " ✅ **Python syntax validation passed for all modified files**
Ran Python compilation check on all modified source and test files to verify syntax correctness before test execution. All five files compiled successfully without errors, confirming that the refactored code changes including signature modifications, new helper functions, and test updates are syntactically valid.
~195t 🛠️ 24,392

**18029** 9:21a 🔵 **Phase B ADR Requirements Identified**
Review session discovered ADR specification for Phase B implementation. The ADR defines two major design changes: D3 centralizes scene control by removing worker's `_first_speech_done` implicit scene switching and moving all scene transitions to broadcast_loop via presentation queue. D7 separates broadcast lifecycle responsibilities by converting auto-filler from implicit idle detection triggers to explicit start/stop tasks routed through the presentation queue API. These changes eliminate duplicate scene switching paths and clarify the boundary between broadcast orchestration (broadcast_loop) and presentation execution (body worker).
~352t 🔍 2,636

**18030** 9:22a 🟣 **Phase B Implementation: Scene Control Centralization and Lifecycle Separation**
Phase B implementation (D3 + D7) refactored scene control and broadcast lifecycle responsibilities. The worker previously switched scenes implicitly on first speak via `_first_speech_done` flag and included fallback scene switching in handle_intro; this created duplicate scene switching paths. The new design removes all implicit scene switching from the worker and centralizes control in broadcast_loop handlers. Each phase handler now explicitly queues scene switches via presentation queue with strict success validation using `wait_for_queue_strict`. Auto-filler lifecycle moved from implicit idle detection triggers to explicit start/stop tasks routed through new REST endpoints, allowing broadcast_loop to control when filler content plays. The start_broadcast method was stripped down to pure OBS/RTMP setup, moving caption_clear and auto_filler initialization to run_broadcast_loop entry point where they execute with strict failure detection. Tests verify that speak no longer switches scenes, auto-filler requires explicit queue actions, and startup caption_clear failures trigger technical_failure closure.
~544t 🛠️ 20,317

**18031** " 🔵 **Test Environment Missing google.genai Dependency**
Phase B code review session attempted to run unit tests for broadcast_loop and streamer_action_queue modules but encountered a missing dependency. The conftest.py file imports google.genai types module which is not installed in the current environment. This prevents execution of the comprehensive test suite that was written to validate Phase B changes including auto_filler_start/stop task handling, scene switching strict validation, and caption_clear startup logic. The test files themselves exist and contain appropriate coverage for Phase B features, but environment setup is incomplete.
~264t 🔍 60,850


Access 923k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>