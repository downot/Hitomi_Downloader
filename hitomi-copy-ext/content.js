(function() {
    let isBatchMode = false;

    // 注入全局样式
    const style = document.createElement('style');
    style.textContent = `
        .hitomi-batch-panel {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: white;
            padding: 15px;
            border-radius: 12px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
            z-index: 999999;
            display: flex;
            gap: 10px;
            align-items: center;
            font-family: sans-serif;
            border: 1px solid #eee;
        }
        
        .hitomi-btn {
            padding: 8px 16px;
            font-size: 13px;
            font-weight: bold;
            color: white;
            border: none;
            border-radius: 20px;
            cursor: pointer;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            transition: all 0.2s ease;
            text-transform: uppercase;
        }
        .hitomi-btn:hover {
            transform: scale(1.05);
            box-shadow: 0 4px 8px rgba(0,0,0,0.3);
        }
        
        .hitomi-btn-primary { background: linear-gradient(135deg, #007bff, #0056b3); }
        .hitomi-btn-success { background: linear-gradient(135deg, #28a745, #218838); }
        .hitomi-btn-danger { background: linear-gradient(135deg, #dc3545, #c82333); }

        .hitomi-copy-btn {
            margin-left: 12px;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: bold;
            color: white;
            background: linear-gradient(135deg, #007bff, #0056b3);
            border: none;
            border-radius: 20px;
            cursor: pointer;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            transition: all 0.2s ease;
            vertical-align: middle;
            text-transform: uppercase;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            flex-shrink: 0;
        }
        .hitomi-copy-btn:hover {
            transform: scale(1.05);
            box-shadow: 0 4px 8px rgba(0,0,0,0.3);
        }
        
        body.batch-copy-mode .hitomi-copy-btn {
            background: linear-gradient(135deg, #28a745, #218838);
        }
        body.batch-copy-mode .hitomi-copy-btn[data-selected="false"] {
            background: linear-gradient(135deg, #6c757d, #5a6268);
            opacity: 0.7;
        }
    `;
    document.head.appendChild(style);

    // 浮动面板
    const panel = document.createElement('div');
    panel.className = 'hitomi-batch-panel';
    document.body.appendChild(panel);

    function renderPanel() {
        if (!isBatchMode) {
            panel.innerHTML = `<button class="hitomi-btn hitomi-btn-primary" id="btn-enter-batch">Batch Copy</button>`;
            document.getElementById('btn-enter-batch').onclick = () => {
                isBatchMode = true;
                document.body.classList.add('batch-copy-mode');
                document.querySelectorAll('.hitomi-copy-btn').forEach(btn => {
                    btn.setAttribute('data-selected', 'true');
                    updateBtnUI(btn);
                });
                renderPanel();
            };
        } else {
            const selectedCount = document.querySelectorAll('.hitomi-copy-btn[data-selected="true"]').length;
            panel.innerHTML = `
                <span style="font-size: 14px; font-weight: bold; margin-right: 10px; color: #333;">Selected: ${selectedCount}</span>
                <button class="hitomi-btn hitomi-btn-success" id="btn-do-copy">Copy All</button>
                <button class="hitomi-btn hitomi-btn-danger" id="btn-cancel-batch">Cancel</button>
            `;
            document.getElementById('btn-cancel-batch').onclick = () => {
                isBatchMode = false;
                document.body.classList.remove('batch-copy-mode');
                document.querySelectorAll('.hitomi-copy-btn').forEach(btn => {
                    btn.setAttribute('data-selected', 'true');
                    updateBtnUI(btn);
                });
                renderPanel();
            };
            document.getElementById('btn-do-copy').onclick = () => {
                const ids = Array.from(document.querySelectorAll('.hitomi-copy-btn[data-selected="true"]'))
                                .map(btn => btn.getAttribute('data-id'));
                if (ids.length > 0) {
                    navigator.clipboard.writeText(ids.join(' ')).then(() => {
                        const btn = document.getElementById('btn-do-copy');
                        btn.textContent = 'Copied!';
                        setTimeout(() => {
                            document.getElementById('btn-cancel-batch').click();
                        }, 1000);
                    });
                } else {
                    const btn = document.getElementById('btn-do-copy');
                    btn.textContent = 'No IDs';
                    setTimeout(() => renderPanel(), 1000);
                }
            };
        }
    }

    renderPanel();

    function updateBtnUI(btn) {
        const id = btn.getAttribute('data-id');
        if (isBatchMode) {
            const isSelected = btn.getAttribute('data-selected') === 'true';
            if (isSelected) {
                btn.innerHTML = `<span>✅ Selected</span> <small>${id}</small>`;
            } else {
                btn.innerHTML = `<span>❌ Ignored</span> <small>${id}</small>`;
            }
        } else {
            btn.innerHTML = `<span>Copy ID</span> <small>${id}</small>`;
            btn.style.background = '';
        }
    }

    function addCopyButtons() {
        const headers = document.querySelectorAll('h1.lillie:not([data-copy-added])');
        let added = false;

        headers.forEach(h1 => {
            const link = h1.querySelector('a');
            if (!link) return;

            const href = link.getAttribute('href');
            const match = href.match(/-(\d+)\.html$/);

            if (match && match[1]) {
                const id = match[1];

                // Ensure h1 layout handles long titles
                h1.style.display = 'flex';
                h1.style.alignItems = 'center';
                h1.style.justifyContent = 'space-between';
                h1.style.maxWidth = '100%';

                // Style the link (title) to truncate
                link.style.overflow = 'hidden';
                link.style.textOverflow = 'ellipsis';
                link.style.whiteSpace = 'nowrap';
                link.style.flex = '1';
                link.style.minWidth = '0';

                // 创建精致的复制按钮
                const btn = document.createElement('button');
                btn.className = 'hitomi-copy-btn';
                btn.setAttribute('data-id', id);
                btn.setAttribute('data-selected', 'true');
                
                updateBtnUI(btn);

                // 点击逻辑
                btn.onclick = (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    
                    if (isBatchMode) {
                        const isSelected = btn.getAttribute('data-selected') === 'true';
                        btn.setAttribute('data-selected', isSelected ? 'false' : 'true');
                        updateBtnUI(btn);
                        renderPanel(); // Update count
                    } else {
                        navigator.clipboard.writeText(id).then(() => {
                            const originalHTML = btn.innerHTML;
                            btn.innerHTML = '✅ Copied!';
                            btn.style.background = 'linear-gradient(135deg, #28a745, #218838)';

                            setTimeout(() => {
                                btn.innerHTML = originalHTML;
                                btn.style.background = ''; // revert to css
                            }, 1000);
                        });
                    }
                };

                h1.appendChild(btn);
                h1.setAttribute('data-copy-added', 'true');
                added = true;
            }
        });

        if (added && isBatchMode) {
            renderPanel();
        }
    }

    // 初始化与动态监听
    addCopyButtons();
    const observer = new MutationObserver(addCopyButtons);
    observer.observe(document.body, { childList: true, subtree: true });
})();
