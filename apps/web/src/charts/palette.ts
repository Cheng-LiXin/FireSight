/** 图表调色板。
 *
 * ECharts 走 canvas，读不到 CSS 变量，所以这里必须镜像 tokens.css 的数值。
 * 改动任何一项都要两侧同步，并重跑 dataviz 校验器：
 *
 *   node <dataviz>/scripts/validate_palette.js "#00A3C4,#D14A17" \
 *        --mode dark --surface "#221C15"
 *
 * 当前状态（surface #221C15）：六项全过
 *   L 均落在 0.48–0.67 · C >= 0.10 · CVD deutan ΔE 21.1 · 常视觉 ΔE 30.8 · 对比度 >= 3:1
 */
export const PALETTE = {
  /** 面板底色，用作堆叠段之间的 2px 间隙色 */
  surface: '#221C15',
  hairline: '#332A20',
  /** 主文字：单序列折线用它——不是类别身份，所以不占类别槽位 */
  ink: '#E9E1D4',
  /** 次要文字：轴标签、图例 */
  inkMuted: '#8C8172',

  /** 类别槽位（固定顺序，不得循环或按排名分配） */
  catSmoke: '#00A3C4',
  catFire: '#D14A17',
} as const

/** 轴标签用等宽：它们需要纵向对齐，这正是等宽该出场的地方。 */
export const AXIS_FONT = "'IBM Plex Mono', Consolas, monospace"
