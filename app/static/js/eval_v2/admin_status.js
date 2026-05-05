// ── eval_v2 admin — TAB 1: 평가 현황 & 상세 모달 & 제출현황 ──────────────────
// This file has been split into 4 focused modules loaded by status.html:
//   admin_status_data.js    — loadStatus, applyFilters, matchesCampusSearch
//   admin_status_render.js  — renderStatus, renderTypeAccordion, renderFlatList, renderRoleAverages, renderResponses, togglePositionFilter, setTypeFilter
//   admin_status_modal.js   — openDetailModal, loadModalResponses, onModalSessionChange, openEditManualModal, openManualInputModal, _loadManualQuestions, selectManualScore, submitManualInput
//   admin_status_actions.js — openSubmissionsModal, generateReport, trashReport, deleteResponse, toggleTestMark, exportCsv, retranslateResponse, openTranslationEdit, saveTranslationEdit
// (refreshRosterCache / refreshNtCache 는 admin_common.js 로 이동 — admin.html 과 status.html 공용)
