// ── Campus Constants (single source of truth) ──────────────────────────────
// Korean name → English code mapping
const CAMPUS_EN = {
  'Campus A':'CMA','Campus B':'CMB','Campus C':'CMC','Campus D':'CMD','Campus E':'CME',
  'Campus F':'CMF','Campus G':'CMG','Campus H':'CMH','Campus I':'CMI','Campus J':'CMJ',
  'Campus K':'CMK','Campus L':'CML','Campus M':'CMM','SUB':'SUB'
};

// Ordered campus list (for dropdowns, tables, etc.)
const CAMPUS_ORDER = [
  'Campus A','Campus B','Campus C','Campus D','Campus E','Campus F',
  'Campus G','Campus H','Campus I','Campus J','Campus K','Campus L','Campus M','SUB'
];

// Role → campus assignment rules
const CAMPUS_REQUIRED_ROLES = ['NET', 'GS', 'TL'];
const CAMPUS_FIXED_ROLES    = { STL: 'SUB' };
