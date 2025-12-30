/* assets/app.js */

// 1. 模擬資料 (因為你還沒提供 data/*.js)
const mockData = {
    aunit: [
        { title: "長照A單位 - 萬華區中心", addr: "台北市萬華區...", tel: "02-1234-5678" },
        { title: "長照A單位 - 大安區辦事處", addr: "台北市大安區...", tel: "02-8765-4321" }
    ],
    subsidy: [
        { title: "輪椅補助申請", desc: "符合長照2.0資格者，最高補助..." },
        { title: "居家無障礙修繕", desc: "扶手、斜坡板安裝補助資訊..." }
    ],
    vendors: [
        { title: "燿翔特約輔具行", type: "特約廠商", area: "全台北" },
        { title: "大台北醫療器材", type: "特約廠商", area: "中山區" }
    ],
    resources: [
        { title: "日間照顧中心列表", category: "長照資源" },
        { title: "身心障礙者支持中心", category: "身障資源" }
    ]
};

document.addEventListener('DOMContentLoaded', () => {
    // 初始化
    initNavigation();
    initModal();
    
    // 預設載入第一個頁面
    loadRoute('aunit');
});

// 處理導覽列點擊
function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    
    navItems.forEach(btn => {
        btn.addEventListener('click', (e) => {
            // 移除所有 active class
            navItems.forEach(i => i.classList.remove('active'));
            // 加上當前 active
            const target = e.currentTarget;
            target.classList.add('active');
            
            // 讀取路由並載入內容
            const route = target.dataset.route;
            loadRoute(route);
        });
    });
}

// 載入內容邏輯
function loadRoute(routeKey) {
    const titleEl = document.getElementById('panelTitle');
    const descEl = document.getElementById('panelDesc');
    const gridEl = document.getElementById('grid');
    
    // 設定標題
    const titles = {
        'aunit': 'A 單位名冊',
        'subsidy': '輔具無障礙補助資訊',
        'vendors': '特約廠商列表',
        'resources': '社區資源總覽'
    };
    
    titleEl.textContent = titles[routeKey] || '查詢結果';
    descEl.textContent = `顯示關於 ${titles[routeKey]} 的資料`;
    
    // 清空並產生卡片
    gridEl.innerHTML = '';
    const data = mockData[routeKey] || [];
    
    data.forEach(item => {
        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `
            <h3>${item.title}</h3>
            <p>${item.addr || item.desc || item.type || ''}</p>
            ${item.tel ? `<p>電話：${item.tel}</p>` : ''}
        `;
        gridEl.appendChild(card);
    });
}

// Modal 開關邏輯
function initModal() {
    const modal = document.getElementById('modal');
    const openBtn = document.getElementById('openHelp');
    const closeElements = document.querySelectorAll('[data-close="1"]');
    
    openBtn.addEventListener('click', () => {
        modal.setAttribute('aria-hidden', 'false');
    });
    
    closeElements.forEach(el => {
        el.addEventListener('click', () => {
            modal.setAttribute('aria-hidden', 'true');
        });
    });
}