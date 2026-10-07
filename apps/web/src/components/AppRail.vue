<script setup lang="ts">
/**
 * 左侧图标轨 —— 全站一级导航。
 *
 * 为什么是图标轨而不是文字菜单：
 *   1) 这个项目是值班用的，七个模块里值班人每天真正会打开的只有三四个
 *      （仪表盘 / 识别 / 监控 / 告警）。文字菜单把七项平等摆开，等于
 *      每天都要在七项里找四项。图标轨靠「分组 + 浮出标签」表达轻重。
 *   2) 文字横向导航（顶栏胶囊）已经试过一轮，问题是它和页面顶部的
 *      取景器刻度角打架：横向一排胶囊全是圆角，和全站刻意的直角签名
 *      不在一个语言里。图标轨是竖直的，签名能立在轨道两侧。
 *
 * 三个刻意的不做：
 *   - 不用 emoji。emoji 是系统字体里借来的，字重、基线、配色都不可控，
 *     而且每个平台渲染不同。图标全部自绘，1.5px 描边、单色、跟着 --bone 走。
 *   - 不用背景色块表示激活。当前位置用「图标变实色 + 左侧 2px 告警色竖标」
 *     表达——竖标是本项目唯一允许的彩色，且它出现在这里语义是准的：
 *     「你正盯着火情」而不是「你点了个按钮」。
 *   - 不折叠成 0 宽。折叠后留 --rail-w-collapsed 的窄边，点它就回来。
 *     收成 0 的话折叠就成了单向操作，用户再也回不来。
 */
import { computed, onMounted, ref, watch } from 'vue'

const NAV = [
  {
    group: '值守',
    items: [
      { to: '/', label: '仪表盘', icon: 'gauge' },
      { to: '/detect', label: '识别检测', icon: 'frame' },
      { to: '/monitor', label: '实时监控', icon: 'wave' },
      { to: '/events', label: '告警事件', icon: 'ledger' },
    ],
  },
  {
    group: '资产',
    items: [
      { to: '/training', label: '训练中心', icon: 'gear' },
      { to: '/models', label: '模型库', icon: 'stack' },
      { to: '/reports', label: '分析报告', icon: 'doc' },
    ],
  },
] as const

/** 月度大屏不在轨里：它整页脱壳，点了人就离开了控制台。 */
const SCREEN_TO = '/bigscreen'

const COLLAPSE_KEY = 'firesight.rail.collapsed'
const collapsed = ref(false)

onMounted(() => {
  // 读取放在 onMounted：这是纯客户端偏好，SSR/首帧不需要它，
  // 放 setup 里会让折叠态在首帧就闪一下再跳。
  try {
    collapsed.value = localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    // localStorage 在某些隐私模式下会抛。偏好读不到就用展开态，
    // 不值得为它报错——折叠只是个便利功能。
  }
})

watch(collapsed, (v) => {
  try {
    localStorage.setItem(COLLAPSE_KEY, v ? '1' : '0')
  } catch {
    // 同上：存不进去只是下次不记住折叠，不影响本次使用。
  }
})

const railClass = computed(() => (collapsed.value ? 'fs-rail is-collapsed' : 'fs-rail'))
</script>

<template>
  <nav class="rail" :class="railClass" aria-label="主导航">
    <RouterLink to="/" class="fs-rail__brand" aria-label="FireSight">
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
        <path
          d="M4 7.5V4.5A.5.5 0 0 1 4.5 4h3M20 7.5V4.5a.5.5 0 0 0-.5-.5h-3M4 16.5v3a.5.5 0 0 0 .5.5h3M20 16.5v3a.5.5 0 0 1-.5.5h-3"
          fill="none"
          stroke="var(--accent)"
          stroke-width="1.5"
          stroke-linecap="square"
        />
        <circle cx="12" cy="12" r="3" fill="var(--alarm)" />
      </svg>
      <span class="fs-rail__tip">FireSight</span>
    </RouterLink>

    <div class="fs-rail__body">
      <div v-for="grp in NAV" :key="grp.group" class="fs-rail__group">
        <span class="fs-rail__groupname">{{ grp.group }}</span>
        <RouterLink
          v-for="item in grp.items"
          :key="item.to"
          :to="item.to"
          class="fs-rail__link"
          :title="item.label"
        >
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <!-- 仪表盘：四条刻度 + 指针 -->
            <template v-if="item.icon === 'gauge'">
              <path d="M4 17a8 8 0 1 1 16 0" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
              <path d="M12 17l4-5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
              <circle cx="12" cy="17" r="1.2" fill="currentColor" />
            </template>
            <!-- 取景框：四角，取自签名语言 -->
            <template v-else-if="item.icon === 'frame'">
              <path
                d="M4 8V5a1 1 0 0 1 1-1h3M20 8V5a1 1 0 0 0-1-1h-3M4 16v3a1 1 0 0 0 1 1h3M20 16v3a1 1 0 0 1-1 1h-3"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
              />
              <circle cx="12" cy="12" r="2.4" fill="none" stroke="currentColor" stroke-width="1.5" />
            </template>
            <!-- 波形：摄像头进来的连续画面 -->
            <template v-else-if="item.icon === 'wave'">
              <path
                d="M3 12h2.5l2-5 3 10 2.5-7 1.8 4H21"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linejoin="round"
                stroke-linecap="round"
              />
            </template>
            <!-- 账册：一列行 -->
            <template v-else-if="item.icon === 'ledger'">
              <path
                d="M6 5h12M6 10h12M6 15h8"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
              />
              <path d="M4 5v14" fill="none" stroke="var(--alarm)" stroke-width="1.5" stroke-linecap="round" />
            </template>
            <!-- 训练：齿轮 -->
            <template v-else-if="item.icon === 'gear'">
              <circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.5" />
              <path
                d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4L18 18M18 6l-1.6 1.6M7.6 16.4L6 18"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
              />
            </template>
            <!-- 模型库：叠放的权重 -->
            <template v-else-if="item.icon === 'stack'">
              <path
                d="M12 4l7.5 3.6L12 11.2 4.5 7.6 12 4zM4.5 12.2L12 15.8l7.5-3.6M4.5 16.4L12 20l7.5-3.6"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linejoin="round"
              />
            </template>
            <!-- 报告：文档 + 折线 -->
            <template v-else>
              <path
                d="M6 4.5h8l4 4v11a.5.5 0 0 1-.5.5h-11a.5.5 0 0 1-.5-.5v-14a.5.5 0 0 1 .5-.5z"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linejoin="round"
              />
              <path d="M9 14.5l2.2-2.6 1.8 1.6 2-2.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
            </template>
          </svg>
          <span class="fs-rail__tip">{{ item.label }}</span>
        </RouterLink>
      </div>
    </div>

    <div class="fs-rail__foot">
      <RouterLink :to="SCREEN_TO" class="fs-rail__link" title="月度大屏">
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path
            d="M4 20V9M9.5 20V4.5M15 20v-8M20.5 20v-5"
            fill="none"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
          />
        </svg>
        <span class="fs-rail__tip">月度大屏</span>
      </RouterLink>

      <button
        type="button"
        class="fs-rail__toggle"
        :title="collapsed ? '展开导航' : '收起导航'"
        :aria-expanded="!collapsed"
        @click="collapsed = !collapsed"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path
            :d="collapsed ? 'M10 8l4 4-4 4' : 'M14 8l-4 4 4 4'"
            fill="none"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
        <span class="fs-rail__tip">{{ collapsed ? '展开导航' : '收起导航' }}</span>
      </button>
    </div>
  </nav>
</template>

<style scoped>
/* 轨是 chrome 不是内容：永不滚动、永远贴壳左侧。宽度取令牌，是为了让
   App.vue 的 grid 预留完全相同的量——两处各写死 68px 是它们迟早对不上的原因。

   position: relative 是浮出标签的锚点；flex: none 是「轨道不许被内容挤瘦」。
   这里刻意不写 overflow:hidden：它会把浮出标签裁掉，而标签恰恰是这条轨
   在折叠态下唯一的说明。轨的内容高度是算得出来的（约 510px），真放不下时
   让页面长高也比在轨里再套一层滚动条好——嵌套滚动条会同时吃掉滚轮事件。 */
.rail {
  position: relative;
  display: flex;
  flex: none;
  flex-direction: column;
  width: var(--rail-w);
  background: var(--ash);
  border-right: var(--hairline);
  transition: width var(--dur-2) var(--ease);
}

.rail.is-collapsed {
  width: var(--rail-w-collapsed);
}

.fs-rail__brand {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  height: var(--shell-h);
  flex: none;
  text-decoration: none;
  border-bottom: var(--hairline);
}

.fs-rail__body {
  flex: 1;
  padding: var(--sp-3) 0;
}

.fs-rail__group + .fs-rail__group {
  margin-top: var(--sp-3);
  padding-top: var(--sp-3);
  border-top: var(--hairline);
}

/* 分组名只在浮出标签里出现，不占轨道：68px 塞不下两个汉字还带内边距，
   而把它们藏进 hover 会让折叠态下两组彻底分不出来。组与组之间留一道
   发丝线，本身就在说「这是一组」。 */
.fs-rail__groupname {
  display: none;
}

.fs-rail__link,
.fs-rail__toggle {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 44px;
  /* 两级色：静止是次要文字，hover 与激活才升到主文字。
     不用背景色块——同 AppRail 脚本块里的理由。 */
  color: var(--dross);
  text-decoration: none;
  background: transparent;
  border: 0;
  cursor: pointer;
  transition: color var(--dur-1) var(--ease);
}

.fs-rail__link:hover,
.fs-rail__toggle:hover {
  color: var(--bone);
}

/* 激活 = 图标变实色 + 贴轨左边一道 2px 告警色竖标。
   背景色块是最顺手的做法，实做出来却让这条轨变成了第二根导航栏；竖标
   读作「你正盯着这一项」，而这正是值班人唯一需要一眼知道的事。竖标也是
   --alarm 在外壳里唯一出现的地方，它只表示这一个意思。 */
.fs-rail__link.router-link-exact-active {
  color: var(--bone);
}

.fs-rail__link.router-link-exact-active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  width: 2px;
  height: 22px;
  margin-top: -11px;
  background: var(--alarm);
}

.fs-rail__foot {
  flex: none;
  padding-bottom: var(--sp-2);
  border-top: var(--hairline);
}

/* 浮出标签：贴到轨道右侧的真空区，绝不压住它描述的那个图标。
   只做透明度过渡——滑动需要轨道有宽度，而折叠态没有。 */
.fs-rail__tip {
  position: absolute;
  left: calc(100% + var(--sp-2));
  top: 50%;
  transform: translateY(-50%);
  padding: var(--sp-1) var(--sp-2);
  font-size: var(--fs-xs);
  line-height: 1.5;
  color: var(--ash);
  white-space: nowrap;
  background: var(--bone);
  border-radius: var(--radius-sm);
  opacity: 0;
  /* 不可点但可滚过：设 pointer-events:none 让它不吃事件，
     visibility 的切换则保证读屏软件不会把七条标签念出来。 */
  visibility: hidden;
  pointer-events: none;
  transition: opacity var(--dur-1) var(--ease),
    visibility 0s linear var(--dur-1);
  z-index: 20;
}

.fs-rail__link:hover .fs-rail__tip,
.fs-rail__toggle:hover .fs-rail__tip,
.fs-rail__brand:hover .fs-rail__tip {
  opacity: 1;
  visibility: visible;
  transition: opacity var(--dur-1) var(--ease);
}

/* 折叠态：浮出标签照常工作，所以它的样式一个字都不用改。品牌标记是唯一
   会缩小的部分——它仍然是「回仪表盘」的点击目标。 */
.rail.is-collapsed .fs-rail__brand svg {
  width: 18px;
  height: 18px;
}

@media (prefers-reduced-motion: reduce) {
  .rail,
  .fs-rail__tip {
    transition: none;
  }
}
</style>
