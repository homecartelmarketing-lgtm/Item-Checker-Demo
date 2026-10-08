/**
 * Akeneo Item Checker - PIM Application Logic
 * Product Information Management & Side Image Validation Tool
 */

(function () {
  'use strict';

  // Application State
  let products = [];
  let currentProductIndex = 0;
  let activeFilter = 'all'; // 'all' | 'flagged' | 'ready'
  let searchQuery = '';
  let selectedSideIndices = new Set();
  
  // Modal State
  let modalActiveList = []; // Array of { url, tag, title, index }
  let modalCurrentIndex = 0;

  // DOM Elements Cache
  const elements = {
    productList: document.getElementById('productList'),
    productTotalBadge: document.getElementById('productTotalBadge'),
    productSearchInput: document.getElementById('productSearchInput'),
    csvFileInput: document.getElementById('csvFileInput'),
    activeDatasetLabel: document.getElementById('activeDatasetLabel'),
    reloadCsvBtn: document.getElementById('reloadCsvBtn'),
    themeToggleBtn: document.getElementById('themeToggleBtn'),

    // Product Header
    breadcrumbCategory: document.getElementById('breadcrumbCategory'),
    breadcrumbTitle: document.getElementById('breadcrumbTitle'),
    productTitle: document.getElementById('productTitle'),
    productSku: document.getElementById('productSku'),
    productVendor: document.getElementById('productVendor'),
    productCategory: document.getElementById('productCategory'),
    productStatusBadge: document.getElementById('productStatusBadge'),
    productLinksContainer: document.getElementById('productLinksContainer'),
    completenessText: document.getElementById('completenessText'),
    completenessBar: document.getElementById('completenessBar'),

    // Tabs
    tabButtons: document.querySelectorAll('.ak-tab-btn'),
    tabPanes: document.querySelectorAll('.ak-tab-pane'),
    tabMediaCountBadge: document.getElementById('tabMediaCountBadge'),

    // Row 1: Model Photo
    modelPhotoContainer: document.getElementById('modelPhotoContainer'),
    modelMetaList: document.getElementById('modelMetaList'),
    quickSpecsGrid: document.getElementById('quickSpecsGrid'),
    downloadModelPhotoBtn: document.getElementById('downloadModelPhotoBtn'),
    copyModelUrlBtn: document.getElementById('copyModelUrlBtn'),

    // Row 2: Item Side Photos
    sideImagesCountBadge: document.getElementById('sideImagesCountBadge'),
    selectAllSideImgsCheckbox: document.getElementById('selectAllSideImgsCheckbox'),
    markSelectedValidBtn: document.getElementById('markSelectedValidBtn'),
    markSelectedInvalidBtn: document.getElementById('markSelectedInvalidBtn'),
    downloadSelectedZipBtn: document.getElementById('downloadSelectedZipBtn'),
    selectedCountSpan: document.getElementById('selectedCountSpan'),
    sideImagesGrid: document.getElementById('sideImagesGrid'),

    // Product Actions
    copyAllUrlsBtn: document.getElementById('copyAllUrlsBtn'),
    exportCleanCsvBtn: document.getElementById('exportCleanCsvBtn'),
    downloadProductZipBtn: document.getElementById('downloadProductZipBtn'),
    batchExportAllBtn: document.getElementById('batchExportAllBtn'),
    batchAiCheckAllBtn: document.getElementById('batchAiCheckAllBtn'),
    runAiAutoCheckBtn: document.getElementById('runAiAutoCheckBtn'),
    exportObsidianAuditBtn: document.getElementById('exportObsidianAuditBtn'),

    // Other Tabs
    fullSpecsContainer: document.getElementById('fullSpecsContainer'),
    bodyHtmlPreview: document.getElementById('bodyHtmlPreview'),
    variantDescPreview: document.getElementById('variantDescPreview'),

    // Modal
    imageModal: document.getElementById('imageModal'),
    modalImgElem: document.getElementById('modalImgElem'),
    modalImgTag: document.getElementById('modalImgTag'),
    modalImgTitle: document.getElementById('modalImgTitle'),
    modalImgIndex: document.getElementById('modalImgIndex'),
    modalImgUrlText: document.getElementById('modalImgUrlText'),
    modalCloseBtn: document.getElementById('modalCloseBtn'),
    modalPrevBtn: document.getElementById('modalPrevBtn'),
    modalNextBtn: document.getElementById('modalNextBtn'),
    modalDownloadBtn: document.getElementById('modalDownloadBtn'),
    modalCopyUrlBtn: document.getElementById('modalCopyUrlBtn'),

    // Toast
    progressToast: document.getElementById('progressToast'),
    toastTitle: document.getElementById('toastTitle'),
    toastSubtitle: document.getElementById('toastSubtitle'),
    toastProgressFill: document.getElementById('toastProgressFill'),
    toastSpinner: document.getElementById('toastSpinner')
  };

  /**
   * Initialize Application
   */
  async function init() {
    setupEventListeners();
    initTheme();

    // Check if preloaded data exists from data.js
    if (window.INITIAL_PRODUCTS && window.INITIAL_PRODUCTS.length > 0) {
      products = JSON.parse(JSON.stringify(window.INITIAL_PRODUCTS));
      await loadAiResults();
      renderApp();
    } else {
      // Fallback: try fetching InvalidSideImages.csv
      tryFetchCsv();
    }
  }

  /**
   * Load AI Verification Results from ai_results.json
   */
  async function loadAiResults() {
    try {
      const resp = await fetch('ai_results.json?t=' + Date.now());
      if (resp.ok) {
        const aiData = await resp.json();
        applyAiDataToProducts(aiData);
        console.log('AI verification results successfully applied.');
      }
    } catch (e) {
      console.log('No ai_results.json loaded yet.');
    }
  }

  /**
   * Apply AI Verification Data to current products
   */
  function applyAiDataToProducts(aiData) {
    let matchedCount = 0;
    let mismatchCount = 0;
    products.forEach(p => {
      const pAi = aiData[p.handle] || aiData[p.title];
      if (pAi && pAi.item_photos) {
        p.item_photos.forEach((photo, idx) => {
          const aiMatch = pAi.item_photos.find(m => String(m.position) === String(photo.position)) || pAi.item_photos[idx];
          if (aiMatch) {
            photo.ai_score = aiMatch.score;
            photo.ai_status = aiMatch.status;
            photo.ai_reason = aiMatch.reason || '';
            photo.critic_vetoed = Boolean(aiMatch.critic_vetoed);
            photo.critic_notes = aiMatch.critic_notes || '';
            photo.ai_dino = aiMatch.dinov2_score;
            photo.ai_lightglue = aiMatch.lightglue_matches;
            photo.ai_box = aiMatch.box;
            photo.verdict = aiMatch.verdict;
            photo.isValid = Boolean(aiMatch.is_valid !== undefined ? aiMatch.is_valid : (aiMatch.status === 'match' && !aiMatch.critic_vetoed));
            if (photo.isValid) matchedCount++;
            else mismatchCount++;
          }
        });
      }
    });
    return { matchedCount, mismatchCount };
  }

  /**
   * Bounding box overlay on Room Photo
   */
  function showRoomBoxHighlight(box) {
    if (!box || !elements.modelPhotoContainer) return;
    removeRoomBoxHighlight();
    const container = elements.modelPhotoContainer;
    const img = container.querySelector('img');
    if (!img) return;

    const overlay = document.createElement('div');
    overlay.className = 'ak-room-box-overlay';
    overlay.id = 'akActiveRoomBox';

    const natW = img.naturalWidth || 800;
    const natH = img.naturalHeight || 800;
    const [x1, y1, x2, y2] = box;
    const leftPct = (x1 / natW) * 100;
    const topPct = (y1 / natH) * 100;
    const widthPct = ((x2 - x1) / natW) * 100;
    const heightPct = ((y2 - y1) / natH) * 100;

    overlay.style.left = `${leftPct}%`;
    overlay.style.top = `${topPct}%`;
    overlay.style.width = `${widthPct}%`;
    overlay.style.height = `${heightPct}%`;
    overlay.innerHTML = `<span style="position: absolute; top: -20px; left: 0; background: #10B981; color: white; font-size: 10px; font-weight: bold; padding: 2px 6px; border-radius: 3px; letter-spacing: 0.04em;">ITEM DETECTED</span>`;

    container.style.position = 'relative';
    container.appendChild(overlay);
  }

  function removeRoomBoxHighlight() {
    const existing = document.getElementById('akActiveRoomBox');
    if (existing) existing.remove();
  }

  /**
   * Fetch and Parse CSV directly
   */
  function tryFetchCsv() {
    showToast('Loading Catalog', 'Parsing InvalidSideImages.csv...', 20);
    Papa.parse('InvalidSideImages.csv', {
      download: true,
      header: true,
      skipEmptyLines: true,
      complete: function (results) {
        if (results.data && results.data.length > 0) {
          products = parseShopifyRows(results.data);
          hideToast();
          renderApp();
        } else {
          showToast('Error', 'Unable to parse CSV data.', 0, true);
        }
      },
      error: function (err) {
        console.warn('Direct CSV fetch error:', err);
        hideToast();
      }
    });
  }

  /**
   * Parser: Convert raw Shopify/Akeneo CSV rows into hierarchical product structure
   */
  function parseShopifyRows(rows) {
    const productMap = {};

    rows.forEach(row => {
      const handle = (row['Handle'] || '').trim();
      if (!handle) return;

      if (!productMap[handle]) {
        productMap[handle] = {
          handle: handle,
          title: (row['Title'] || handle).trim(),
          sku: (row['SKU'] || '').trim(),
          vendor: (row['Vendor'] || '').trim(),
          category: (row['Product Category'] || row['Type'] || 'Lighting Fixtures').trim(),
          type: (row['Type'] || '').trim(),
          status: (row['Status'] || 'Draft').trim(),
          completeness: (row['CSVEDITOR_Completeness'] || '50').trim(),
          tags: (row['Tags'] || '').trim(),
          body_html: (row['Body (HTML)'] || '').trim(),
          variant_desc: (row['VariantDescription'] || '').trim(),
          product_link: (row['Product Link'] || row['CSVEDITOR_OriginalExcelSourceLink'] || '').trim(),
          shop_link: (row['Shop Link'] || '').trim(),
          specs: {
            material: (row['CSVEDITOR_Material'] || row['PRODINF_Material'] || '').trim(),
            length: (row['CSVEDITOR_Length'] || '').trim(),
            width: (row['CSVEDITOR_Width'] || '').trim(),
            height: (row['CSVEDITOR_Height'] || '').trim(),
            diameter: (row['CSVEDITOR_Diameter'] || '').trim(),
            shape: (row['CSVEDITOR_Shape'] || '').trim(),
            room: (row['CSVEDITOR_Room'] || row['PRODINF_Applicable Space'] || '').trim(),
            style: (row['CSVEDITOR_Style'] || row['PRODINF_Style'] || '').trim(),
            light_source: (row['CSVEDITOR_LightSource'] || '').trim(),
            color: (row['CSVEDITOR_Color'] || '').trim(),
            color_temp: (row['CSVEDITOR_ColorTemperature'] || '').trim(),
            cost: (row['Cost per item'] || '').trim(),
            model: (row['PRODINF_Model'] || '').trim(),
            brand: (row['PRODINF_Brand'] || '').trim(),
            ip_rating: (row['CSVEDITOR_IPRating'] || '').trim(),
            dimmable: (row['CSVEDITOR_Dimmable'] || '').trim(),
            wattage: (row['CSVEDITOR_Wattage'] || '').trim()
          },
          model_photo: null,
          item_photos: []
        };
      }

      const imgSrc = (row['Image Src'] || '').trim();
      const pos = (row['Image Position'] || '').trim();
      const variantImg = (row['Variant Image'] || '').trim();

      if (imgSrc) {
        const photoObj = {
          url: imgSrc,
          position: pos ? pos : 'Unpositioned',
          alt: (row['Image Alt Text'] || '').trim(),
          is_variant_img: Boolean(variantImg && variantImg === imgSrc),
          isValid: true // default valid
        };

        if (productMap[handle].model_photo === null) {
          productMap[handle].model_photo = photoObj;
        } else {
          productMap[handle].item_photos.push(photoObj);
        }
      }
    });

    return Object.values(productMap);
  }

  /**
   * Render Whole Application
   */
  function renderApp() {
    renderSidebar();
    renderCurrentProduct();
  }

  /**
   * Filter products based on search and tab filter
   */
  function getFilteredProducts() {
    return products.filter((p, index) => {
      // Search filter
      const matchesSearch = searchQuery === '' ||
        p.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        p.handle.toLowerCase().includes(searchQuery.toLowerCase()) ||
        p.sku.toLowerCase().includes(searchQuery.toLowerCase()) ||
        p.tags.toLowerCase().includes(searchQuery.toLowerCase());

      if (!matchesSearch) return false;

      // Tab filter
      const hasInvalid = p.item_photos.some(img => !img.isValid);
      if (activeFilter === 'flagged') return hasInvalid;
      if (activeFilter === 'ready') return !hasInvalid;
      return true;
    });
  }

  /**
   * Render Sidebar Product Catalog
   */
  function renderSidebar() {
    elements.productList.innerHTML = '';
    const filtered = getFilteredProducts();

    elements.productTotalBadge.textContent = products.length;

    if (filtered.length === 0) {
      elements.productList.innerHTML = `
        <div style="padding: 24px; text-align: center; color: var(--text-muted); font-size: 13px;">
          <i class="fa-solid fa-box-open" style="font-size: 24px; margin-bottom: 8px; display: block; opacity: 0.5;"></i>
          No products matching criteria
        </div>
      `;
      return;
    }

    filtered.forEach((prod) => {
      const originalIndex = products.indexOf(prod);
      const isActive = originalIndex === currentProductIndex;
      const invalidCount = prod.item_photos.filter(img => !img.isValid).length;
      const totalPhotos = (prod.model_photo ? 1 : 0) + prod.item_photos.length;

      const itemEl = document.createElement('div');
      itemEl.className = `ak-product-item ${isActive ? 'active' : ''}`;
      
      const thumbUrl = prod.model_photo ? prod.model_photo.url : '';

      itemEl.innerHTML = `
        <img class="ak-prod-thumb" src="${escapeHtml(thumbUrl)}" alt="${escapeHtml(prod.title)}" loading="lazy">
        <div class="ak-prod-info">
          <div class="ak-prod-name" title="${escapeHtml(prod.title)}">${escapeHtml(prod.title)}</div>
          <div class="ak-prod-meta">
            <span><i class="fa-regular fa-image"></i> ${totalPhotos} photos</span>
            ${invalidCount > 0 
              ? `<span class="ak-prod-badge" style="background: rgba(239, 68, 68, 0.2); color: #EF4444;"><i class="fa-solid fa-triangle-exclamation"></i> ${invalidCount} flagged</span>` 
              : `<span class="ak-prod-badge"><i class="fa-solid fa-check"></i> ${prod.completeness}%</span>`}
          </div>
        </div>
      `;

      itemEl.addEventListener('click', () => {
        currentProductIndex = originalIndex;
        selectedSideIndices.clear();
        elements.selectAllSideImgsCheckbox.checked = false;
        renderSidebar();
        renderCurrentProduct();
      });

      elements.productList.appendChild(itemEl);
    });
  }

  /**
   * Render Selected Product Details
   */
  function renderCurrentProduct() {
    if (!products || products.length === 0 || !products[currentProductIndex]) {
      return;
    }

    const prod = products[currentProductIndex];

    // Breadcrumb & Meta Header
    elements.breadcrumbCategory.textContent = prod.category || 'Lighting Fixtures';
    elements.breadcrumbTitle.textContent = prod.title;
    elements.productTitle.textContent = prod.title;
    elements.productSku.textContent = prod.sku || 'N/A';
    elements.productVendor.textContent = prod.vendor || 'Unknown Vendor';
    elements.productCategory.textContent = prod.category;
    elements.productStatusBadge.textContent = (prod.status || 'DRAFT').toUpperCase();
    
    // Completeness
    const compVal = parseInt(prod.completeness, 10) || 50;
    elements.completenessText.textContent = `${compVal}%`;
    elements.completenessBar.style.width = `${compVal}%`;

    // External Links
    elements.productLinksContainer.innerHTML = '';
    if (prod.product_link) {
      const link = document.createElement('a');
      link.href = prod.product_link;
      link.target = '_blank';
      link.rel = 'noreferrer';
      link.className = 'ak-meta-link';
      link.innerHTML = `<i class="fa-solid fa-arrow-up-right-from-square"></i> Tmall Item`;
      elements.productLinksContainer.appendChild(link);
    }
    if (prod.shop_link) {
      const shopLink = document.createElement('a');
      shopLink.href = prod.shop_link.startsWith('http') ? prod.shop_link : `https://${prod.shop_link}`;
      shopLink.target = '_blank';
      shopLink.rel = 'noreferrer';
      shopLink.className = 'ak-meta-link';
      shopLink.style.marginLeft = '10px';
      shopLink.innerHTML = `<i class="fa-solid fa-store"></i> Taobao Shop`;
      elements.productLinksContainer.appendChild(shopLink);
    }

    // Media Counts
    const totalMedia = (prod.model_photo ? 1 : 0) + prod.item_photos.length;
    elements.tabMediaCountBadge.textContent = totalMedia;
    elements.sideImagesCountBadge.textContent = `${prod.item_photos.length} Photos`;

    // Render Row 1: Model Photo
    renderModelPhoto(prod);

    // Render Row 2+: Item Side Photos
    renderSideImages(prod);

    // Render Specs Tab
    renderSpecsTab(prod);

    // Render Description Tab
    renderDescTab(prod);

    updateSelectionUI();
  }

  /**
   * Render Row 1: Model Photo Card
   */
  function renderModelPhoto(prod) {
    const model = prod.model_photo;
    elements.modelPhotoContainer.innerHTML = '';

    if (!model || !model.url) {
      elements.modelPhotoContainer.innerHTML = `
        <div style="padding: 40px; text-align: center; color: var(--text-muted);">
          <i class="fa-regular fa-image" style="font-size: 32px; margin-bottom: 8px;"></i>
          <p>No Model Photo Available</p>
        </div>
      `;
      elements.modelMetaList.innerHTML = '<p class="ak-meta-label">No metadata</p>';
      return;
    }

    const img = document.createElement('img');
    img.src = model.url;
    img.alt = model.alt || prod.title;
    img.title = 'Click to open in high-resolution viewer';
    
    img.addEventListener('click', () => {
      openModalForImage(-1); // -1 is model photo
    });

    elements.modelPhotoContainer.appendChild(img);

    // Model Photo Metadata list
    elements.modelMetaList.innerHTML = `
      <div class="ak-kv-item">
        <span>Row Classification</span>
        <strong>Row 1 &bull; Model Hero Photo</strong>
      </div>
      <div class="ak-kv-item">
        <span>Image Position</span>
        <strong>Position ${model.position}</strong>
      </div>
      <div class="ak-kv-item">
        <span>Shopify Variant Image</span>
        <strong>${model.is_variant_img ? 'Matched Variant Image' : 'Standard Gallery'}</strong>
      </div>
      <div class="ak-kv-item">
        <span>CDN Host</span>
        <strong title="${model.url}">${new URL(model.url).hostname}</strong>
      </div>
    `;

    // Quick Specs Grid
    const specs = prod.specs || {};
    const dimensions = [specs.length, specs.width, specs.height].filter(Boolean).join(' × ') || specs.diameter || 'Custom';
    
    elements.quickSpecsGrid.innerHTML = `
      <div class="ak-spec-card">
        <div class="lbl">Dimensions</div>
        <div class="val">${escapeHtml(dimensions)}</div>
      </div>
      <div class="ak-spec-card">
        <div class="lbl">Material</div>
        <div class="val">${escapeHtml(specs.material || 'Metal / Glass')}</div>
      </div>
      <div class="ak-spec-card">
        <div class="lbl">Applicable Space</div>
        <div class="val">${escapeHtml(specs.room || 'Living Room / Hall')}</div>
      </div>
      <div class="ak-spec-card">
        <div class="lbl">Light Source</div>
        <div class="val">${escapeHtml(specs.light_source || 'E14 / LED')}</div>
      </div>
      <div class="ak-spec-card">
        <div class="lbl">Style</div>
        <div class="val">${escapeHtml(specs.style || 'Modern Luxury')}</div>
      </div>
      <div class="ak-spec-card">
        <div class="lbl">Model Code</div>
        <div class="val">${escapeHtml(specs.model || prod.sku || '-')}</div>
      </div>
    `;

    // Model Action Buttons
    elements.downloadModelPhotoBtn.onclick = () => {
      downloadSingleImage(model.url, `${sanitizeFilename(prod.handle)}_01_MODEL_PHOTO.jpg`);
    };

    elements.copyModelUrlBtn.onclick = () => {
      copyToClipboard(model.url, 'Model photo URL copied!');
    };
  }

  /**
   * Render Row 2+: Item Side Photos Grid
   */
  function renderSideImages(prod) {
    elements.sideImagesGrid.innerHTML = '';
    const items = prod.item_photos;

    if (!items || items.length === 0) {
      elements.sideImagesGrid.innerHTML = `
        <div style="grid-column: 1 / -1; padding: 40px; text-align: center; color: var(--text-muted); background: var(--bg-surface-subtle); border-radius: var(--radius-md);">
          <i class="fa-regular fa-images" style="font-size: 28px; margin-bottom: 8px; opacity: 0.5;"></i>
          <p>No additional side photos found for this product.</p>
        </div>
      `;
      return;
    }

    items.forEach((photo, idx) => {
      const isSelected = selectedSideIndices.has(idx);
      const isFlaggedInvalid = !photo.isValid;
      const isAiMatch = photo.ai_score !== undefined && (photo.ai_status === 'match' || photo.verdict === 'valid');
      const isAiMismatch = photo.ai_score !== undefined && (photo.ai_status === 'mismatch' || photo.verdict === 'invalid');
      const isAiReview = photo.ai_score !== undefined && (photo.ai_status === 'review' || photo.verdict === 'review');

      const card = document.createElement('div');
      card.className = `ak-side-card ${isSelected ? 'selected' : ''} ${isFlaggedInvalid && !isAiReview ? 'invalid-flagged' : ''} ${isAiMatch ? 'ai-match' : ''} ${isAiMismatch ? 'ai-mismatch' : ''} ${isAiReview ? 'ai-review' : ''}`;
      
      const posLabel = photo.position && photo.position !== 'Unpositioned' ? `Pos #${photo.position}` : `Shot #${idx + 2}`;

      // AI Badge & Reasoning HTML
      let aiBadgeHtml = '';
      let aiMetricsHtml = '';
      if (photo.ai_score !== undefined) {
        if (photo.critic_vetoed) {
          aiBadgeHtml = `<span class="ak-ai-badge mismatch" style="background: #FFE4E6; color: #BE123C; border-color: #FDA4AF;" title="${escapeHtml(photo.ai_reason || '')}"><i class="fa-solid fa-gavel"></i> CRITIC VETO</span>`;
        } else if (photo.ai_status === 'match' || photo.verdict === 'valid') {
          aiBadgeHtml = `<span class="ak-ai-badge match" title="${escapeHtml(photo.ai_reason || '')}"><i class="fa-solid fa-circle-check"></i> ${photo.ai_score}% MATCH</span>`;
        } else if (photo.ai_status === 'review' || photo.verdict === 'review') {
          aiBadgeHtml = `<span class="ak-ai-badge review" style="background: #FEF3C7; color: #B45309; border: 1px solid #FCD34D;" title="${escapeHtml(photo.ai_reason || '')}"><i class="fa-solid fa-eye"></i> NEEDS REVIEW (${photo.ai_score}%)</span>`;
        } else {
          aiBadgeHtml = `<span class="ak-ai-badge mismatch" title="${escapeHtml(photo.ai_reason || '')}"><i class="fa-solid fa-triangle-exclamation"></i> MISMATCH (${photo.ai_score}%)</span>`;
        }

        let reasonHtml = '';
        if (photo.ai_reason) {
          const isMatch = (photo.ai_status === 'match' || photo.verdict === 'valid');
          const isReview = (photo.ai_status === 'review' || photo.verdict === 'review');
          const reasonClass = isMatch ? 'match' : (isReview ? 'review' : 'mismatch');
          const reasonIcon = isMatch ? 'fa-solid fa-circle-check' : (isReview ? 'fa-solid fa-eye' : 'fa-solid fa-triangle-exclamation');
          const reasonStyle = isReview ? 'style="background: #FFFBEB; border-color: #FCD34D; color: #92400E;"' : '';
          reasonHtml = `
            <div class="ak-card-ai-reason ${reasonClass}" ${reasonStyle}>
              <i class="${reasonIcon}"></i>
              <span>${escapeHtml(photo.ai_reason)}</span>
            </div>
          `;
        }

        let criticHtml = '';
        if (photo.critic_notes) {
          criticHtml = `
            <div class="ak-card-critic-note">
              <i class="fa-solid fa-gavel"></i>
              <span><strong>Critic Audit:</strong> ${escapeHtml(photo.critic_notes)}</span>
            </div>
          `;
        }

        let techMetricsHtml = '';
        if (photo.ai_dino !== undefined && photo.ai_lightglue !== undefined) {
          techMetricsHtml = `
            <div class="ak-card-ai-metrics">
              <span><i class="fa-solid fa-palette"></i> DINO: ${photo.ai_dino || 0}%</span>
              <span><i class="fa-solid fa-shapes"></i> LG: ${photo.ai_lightglue || 0} pts</span>
            </div>
          `;
        }

        aiMetricsHtml = `
          ${reasonHtml}
          ${criticHtml}
          ${techMetricsHtml}
        `;
      }

      card.innerHTML = `
        <div class="ak-card-top-bar">
          <span class="ak-pos-badge">${posLabel}</span>
          ${aiBadgeHtml}
          <input type="checkbox" class="ak-card-select-input" data-index="${idx}" ${isSelected ? 'checked' : ''}>
        </div>

        <div class="ak-side-img-box" data-index="${idx}">
          <img src="${escapeHtml(photo.url)}" alt="Item side photo ${idx + 1}" loading="lazy">
        </div>

        ${aiMetricsHtml}

        <div class="ak-card-footer">
          <button class="ak-validation-toggle-btn ${photo.isValid ? 'is-valid' : (photo.verdict === 'review' ? 'is-review' : 'is-invalid')}" data-index="${idx}" title="Click to toggle validity status">
            ${photo.isValid 
              ? `<i class="fa-solid fa-circle-check"></i> Valid Side Photo` 
              : (photo.verdict === 'review' ? `<i class="fa-solid fa-eye"></i> Needs Review` : `<i class="fa-solid fa-triangle-exclamation"></i> Invalid Side Image`)}
          </button>

          <div class="ak-card-mini-actions">
            <button class="btn-inspect" data-index="${idx}" title="Preview full size">
              <i class="fa-solid fa-expand"></i> View
            </button>
            <button class="btn-download-one" data-index="${idx}" title="Download this photo">
              <i class="fa-solid fa-download"></i> Save
            </button>
            <button class="btn-copy-one" data-index="${idx}" title="Copy photo link">
              <i class="fa-regular fa-copy"></i>
            </button>
          </div>
        </div>
      `;

      // Hover over card highlights SAM candidate box on Room Photo if available
      card.addEventListener('mouseenter', () => {
        if (photo.ai_box && elements.modelPhotoContainer) {
          showRoomBoxHighlight(photo.ai_box);
        }
      });
      card.addEventListener('mouseleave', () => {
        removeRoomBoxHighlight();
      });

      // Click card image to inspect
      card.querySelector('.ak-side-img-box').addEventListener('click', () => {
        openModalForImage(idx);
      });

      // Checkbox click
      const checkbox = card.querySelector('.ak-card-select-input');
      checkbox.addEventListener('change', (e) => {
        if (e.target.checked) {
          selectedSideIndices.add(idx);
        } else {
          selectedSideIndices.delete(idx);
        }
        updateSelectionUI();
      });

      // Validation Toggle & Active Learning Memory Sync
      const toggleBtn = card.querySelector('.ak-validation-toggle-btn');
      toggleBtn.addEventListener('click', () => {
        photo.isValid = !photo.isValid;
        photo.human_override = true;
        renderSideImages(prod);
        renderSidebar(); // update flagged counter on sidebar
        saveFeedbackToKnowledgeBase(prod.handle, photo.url, photo.position, photo.isValid);
      });

      // Mini actions
      card.querySelector('.btn-inspect').addEventListener('click', () => openModalForImage(idx));
      card.querySelector('.btn-download-one').addEventListener('click', () => {
        downloadSingleImage(photo.url, `${sanitizeFilename(prod.handle)}_side_pos_${photo.position || idx + 2}.jpg`);
      });
      card.querySelector('.btn-copy-one').addEventListener('click', () => {
        copyToClipboard(photo.url, 'Image URL copied!');
      });

      elements.sideImagesGrid.appendChild(card);
    });
  }

  /**
   * Render Specifications Tab
   */
  function renderSpecsTab(prod) {
    const specs = prod.specs || {};
    const rows = [
      ['Product Title', prod.title],
      ['Shopify Handle', prod.handle],
      ['Product SKU', prod.sku],
      ['Vendor / Brand', `${prod.vendor || ''} ${specs.brand ? `(${specs.brand})` : ''}`],
      ['Category / Akeneo Code', prod.category],
      ['Dimensions (L × W × H)', `${specs.length || '-'} × ${specs.width || '-'} × ${specs.height || '-'}`],
      ['Diameter', specs.diameter || 'N/A'],
      ['Material Composition', specs.material || '-'],
      ['Shape', specs.shape || '-'],
      ['Applicable Room / Space', specs.room || '-'],
      ['Interior Style', specs.style || '-'],
      ['Light Source / Bulb', specs.light_source || '-'],
      ['Color / Finish', specs.color || '-'],
      ['Color Temperature', specs.color_temp || '-'],
      ['IP Protection Rating', specs.ip_rating || '-'],
      ['Dimmable Support', specs.dimmable === '1.0' || specs.dimmable === '1' ? 'Yes' : 'No'],
      ['Wattage', specs.wattage || '-'],
      ['Cost Per Item', specs.cost || '-'],
      ['Manufacturer Model', specs.model || '-']
    ];

    let html = `<table class="ak-specs-table">
      <thead>
        <tr>
          <th style="width: 35%;">Akeneo Attribute</th>
          <th>Value</th>
        </tr>
      </thead>
      <tbody>`;

    rows.forEach(([key, val]) => {
      html += `
        <tr>
          <td><strong>${escapeHtml(key)}</strong></td>
          <td>${escapeHtml(val || '-')}</td>
        </tr>
      `;
    });

    html += `</tbody></table>`;
    elements.fullSpecsContainer.innerHTML = html;
  }

  /**
   * Render Description Tab
   */
  function renderDescTab(prod) {
    elements.bodyHtmlPreview.innerHTML = prod.body_html || '<p style="color: var(--text-muted);">No HTML description available.</p>';
    elements.variantDescPreview.textContent = prod.variant_desc || 'No variant description text.';
  }

  /**
   * Update Selection Counters & Checkboxes
   */
  function updateSelectionUI() {
    const prod = products[currentProductIndex];
    if (!prod) return;

    const total = prod.item_photos.length;
    const selectedCount = selectedSideIndices.size;

    elements.selectedCountSpan.textContent = selectedCount;
    elements.selectAllSideImgsCheckbox.checked = total > 0 && selectedCount === total;
    elements.selectAllSideImgsCheckbox.indeterminate = selectedCount > 0 && selectedCount < total;

    // Highlight selected cards in DOM
    document.querySelectorAll('.ak-card-select-input').forEach(cb => {
      const idx = parseInt(cb.getAttribute('data-index'), 10);
      const isChecked = selectedSideIndices.has(idx);
      cb.checked = isChecked;
      const card = cb.closest('.ak-side-card');
      if (card) {
        if (isChecked) card.classList.add('selected');
        else card.classList.remove('selected');
      }
    });
  }

  /**
   * Setup Event Listeners
   */
  function setupEventListeners() {
    // Search input
    elements.productSearchInput.addEventListener('input', (e) => {
      searchQuery = e.target.value.trim();
      renderSidebar();
    });

    // Sidebar filter buttons
    document.querySelectorAll('.ak-filter-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        document.querySelectorAll('.ak-filter-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        activeFilter = tab.getAttribute('data-filter');
        renderSidebar();
      });
    });

    // Reload CSV button
    elements.reloadCsvBtn.addEventListener('click', () => {
      if (window.INITIAL_PRODUCTS) {
        products = JSON.parse(JSON.stringify(window.INITIAL_PRODUCTS));
        currentProductIndex = 0;
        selectedSideIndices.clear();
        renderApp();
        showToast('Reset Done', 'Reloaded default InvalidSideImages.csv dataset.', 100);
      }
    });

    // CSV File Upload
    elements.csvFileInput.addEventListener('change', (e) => {
      const file = e.target.files[0];
      if (!file) return;

      showToast('Uploading CSV', `Reading ${file.name}...`, 30);
      Papa.parse(file, {
        header: true,
        skipEmptyLines: true,
        complete: function (results) {
          if (results.data && results.data.length > 0) {
            products = parseShopifyRows(results.data);
            currentProductIndex = 0;
            selectedSideIndices.clear();
            elements.activeDatasetLabel.textContent = file.name;
            renderApp();
            showToast('Loaded Successfully', `Imported ${products.length} products from ${file.name}.`, 100);
          } else {
            showToast('Error', 'No valid product data found in CSV.', 0, true);
          }
        },
        error: function (err) {
          showToast('Error', 'Failed to parse CSV file: ' + err.message, 0, true);
        }
      });
    });

    // Theme toggle
    elements.themeToggleBtn.addEventListener('click', toggleTheme);

    // Nav Tabs switching
    elements.tabButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        elements.tabButtons.forEach(b => b.classList.remove('active'));
        elements.tabPanes.forEach(p => p.classList.remove('active'));

        btn.classList.add('active');
        const tabKey = btn.getAttribute('data-tab');
        if (tabKey === 'media') document.getElementById('tabPaneMedia').classList.add('active');
        if (tabKey === 'specs') document.getElementById('tabPaneSpecs').classList.add('active');
        if (tabKey === 'description') document.getElementById('tabPaneDesc').classList.add('active');
      });
    });

    // Select All checkbox
    elements.selectAllSideImgsCheckbox.addEventListener('change', (e) => {
      const prod = products[currentProductIndex];
      if (!prod) return;

      if (e.target.checked) {
        prod.item_photos.forEach((_, idx) => selectedSideIndices.add(idx));
      } else {
        selectedSideIndices.clear();
      }
      updateSelectionUI();
    });

    // Mark Selected Valid
    elements.markSelectedValidBtn.addEventListener('click', () => {
      const prod = products[currentProductIndex];
      if (!prod || selectedSideIndices.size === 0) {
        showToast('Notice', 'Please select one or more side photos first.', 0);
        return;
      }
      selectedSideIndices.forEach(idx => {
        if (prod.item_photos[idx]) {
          prod.item_photos[idx].isValid = true;
          prod.item_photos[idx].human_override = true;
          saveFeedbackToKnowledgeBase(prod.handle, prod.item_photos[idx].url, prod.item_photos[idx].position, true);
        }
      });
      renderSideImages(prod);
      renderSidebar();
      showToast('Status Updated', `Marked ${selectedSideIndices.size} photos as Valid.`, 100);
    });

    // Mark Selected Invalid
    elements.markSelectedInvalidBtn.addEventListener('click', () => {
      const prod = products[currentProductIndex];
      if (!prod || selectedSideIndices.size === 0) {
        showToast('Notice', 'Please select one or more side photos first.', 0);
        return;
      }
      selectedSideIndices.forEach(idx => {
        if (prod.item_photos[idx]) {
          prod.item_photos[idx].isValid = false;
          prod.item_photos[idx].human_override = true;
          saveFeedbackToKnowledgeBase(prod.handle, prod.item_photos[idx].url, prod.item_photos[idx].position, false);
        }
      });
      renderSideImages(prod);
      renderSidebar();
      showToast('Flagged Invalid', `Marked ${selectedSideIndices.size} photos as Invalid Side Images.`, 100);
    });

    // Copy All Image URLs
    elements.copyAllUrlsBtn.addEventListener('click', () => {
      const prod = products[currentProductIndex];
      if (!prod) return;

      const urls = [];
      if (prod.model_photo && prod.model_photo.url) {
        urls.push(`Model Photo (Pos 1): ${prod.model_photo.url}`);
      }
      prod.item_photos.forEach((img, i) => {
        urls.push(`Side Photo (Pos ${img.position || i + 2}): ${img.url}`);
      });

      copyToClipboard(urls.join('\n'), `Copied ${urls.length} image URLs to clipboard!`);
    });

    // Export Cleaned CSV
    elements.exportCleanCsvBtn.addEventListener('click', () => {
      exportValidatedCsv();
    });

    // Download Single Product ZIP
    elements.downloadProductZipBtn.addEventListener('click', () => {
      const prod = products[currentProductIndex];
      if (prod) downloadProductAsZip(prod);
    });

    // Download Selected Side Photos ZIP
    elements.downloadSelectedZipBtn.addEventListener('click', () => {
      const prod = products[currentProductIndex];
      if (!prod) return;
      if (selectedSideIndices.size === 0) {
        showToast('Notice', 'Please check at least one photo to download.', 0);
        return;
      }
      downloadSelectedPhotosZip(prod);
    });

    // Batch Export All Products ZIP
    elements.batchExportAllBtn.addEventListener('click', () => {
      batchExportAllProductsZip();
    });

    // Single Product AI Check (Qwen 3.8 Flash Vision)
    document.addEventListener('click', (e) => {
      const btn = e.target && e.target.closest('#runAiAutoCheckBtn');
      if (btn) {
        const prod = products[currentProductIndex];
        if (prod) {
          startAiStreamVerification(prod.handle);
        }
      }
    });

    // Batch Catalog AI Check (Qwen 3.8 Flash Vision)
    if (elements.batchAiCheckAllBtn) {
      elements.batchAiCheckAllBtn.addEventListener('click', () => {
        const confirmCheck = confirm(`Start Qwen 3.8 Flash Vision AI verification for all ${products.length} products in the catalog?`);
        if (confirmCheck) {
          startAiStreamVerification(null);
        }
      });
    }

    // Export Obsidian Markdown Audit Report
    if (elements.exportObsidianAuditBtn) {
      elements.exportObsidianAuditBtn.addEventListener('click', async () => {
        showToast('Obsidian Export', 'Generating markdown audit report in knowledge/audits/...', 30);
        try {
          const resp = await fetch('/api/export_obsidian_audit');
          if (resp.ok) {
            const data = await resp.json();
            showToast('Obsidian Report Ready', 'Saved to knowledge/audits/catalog_audit_latest.md', 100);
          } else {
            showToast('Export Notice', 'Check knowledge/audits/ directory.', 100);
          }
        } catch (e) {
          showToast('Notice', 'Report generated.', 100);
        }
      });
    }

    // Modal Controls
    elements.modalCloseBtn.addEventListener('click', closeModal);
    elements.imageModal.addEventListener('click', (e) => {
      if (e.target === elements.imageModal) closeModal();
    });
    elements.modalPrevBtn.addEventListener('click', () => navigateModal(-1));
    elements.modalNextBtn.addEventListener('click', () => navigateModal(1));

    // Keyboard navigation (Shortcuts for high-throughput reviewing)
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

      if (elements.imageModal.classList.contains('open')) {
        if (e.key === 'Escape') closeModal();
        if (e.key === 'ArrowLeft') navigateModal(-1);
        if (e.key === 'ArrowRight') navigateModal(1);
      } else {
        // Product switching
        if (e.key === 'ArrowRight' || e.key === 'j') {
          if (currentProductIndex < products.length - 1) {
            selectProduct(currentProductIndex + 1);
          }
        } else if (e.key === 'ArrowLeft' || e.key === 'k') {
          if (currentProductIndex > 0) {
            selectProduct(currentProductIndex - 1);
          }
        } else if (e.key.toLowerCase() === 'v') {
          // Quick Mark Valid shortcut
          const prod = products[currentProductIndex];
          if (prod && selectedSideIndices.size > 0) {
            selectedSideIndices.forEach(idx => { if (prod.item_photos[idx]) prod.item_photos[idx].isValid = true; });
            renderSideImages(prod);
            renderSidebar();
            showToast('Quick Valid', `Marked ${selectedSideIndices.size} photos as Valid.`, 100);
          }
        } else if (e.key.toLowerCase() === 'x') {
          // Quick Mark Invalid shortcut
          const prod = products[currentProductIndex];
          if (prod && selectedSideIndices.size > 0) {
            selectedSideIndices.forEach(idx => { if (prod.item_photos[idx]) prod.item_photos[idx].isValid = false; });
            renderSideImages(prod);
            renderSidebar();
            showToast('Quick Invalid', `Marked ${selectedSideIndices.size} photos as Invalid Side Images.`, 100);
          }
        }
      }
    });
  }

  /**
   * Modal: Open high resolution preview
   */
  function openModalForImage(targetIndex) {
    const prod = products[currentProductIndex];
    if (!prod) return;

    modalActiveList = [];

    // Add model photo as item 0
    if (prod.model_photo && prod.model_photo.url) {
      modalActiveList.push({
        url: prod.model_photo.url,
        tag: 'Row 1 • Model Photo',
        title: `${prod.title} (Hero Presentation)`,
        isModel: true
      });
    }

    // Add item photos
    prod.item_photos.forEach((img, idx) => {
      modalActiveList.push({
        url: img.url,
        tag: `Row 2 • Pos #${img.position || idx + 2}`,
        title: `${prod.title} (Side Photo #${idx + 1})`,
        isModel: false,
        isValid: img.isValid
      });
    });

    // targetIndex: -1 means model photo (index 0 in list), >= 0 means item photo (index + 1)
    modalCurrentIndex = targetIndex === -1 ? 0 : targetIndex + (prod.model_photo ? 1 : 0);
    if (modalCurrentIndex >= modalActiveList.length) modalCurrentIndex = 0;

    renderModalContent();
    elements.imageModal.classList.add('open');
  }

  function renderModalContent() {
    const item = modalActiveList[modalCurrentIndex];
    if (!item) return;

    elements.modalImgElem.src = item.url;
    elements.modalImgTag.textContent = item.tag;
    elements.modalImgTag.className = `ak-badge ${item.isModel ? 'ak-badge-primary' : (item.isValid !== false ? 'ak-badge-neutral' : 'ak-btn-danger')}`;
    elements.modalImgTitle.textContent = item.title;
    elements.modalImgIndex.textContent = `${modalCurrentIndex + 1} / ${modalActiveList.length}`;
    elements.modalImgUrlText.textContent = item.url;

    elements.modalDownloadBtn.onclick = () => {
      downloadSingleImage(item.url, `photo_${modalCurrentIndex + 1}.jpg`);
    };

    elements.modalCopyUrlBtn.onclick = () => {
      copyToClipboard(item.url, 'Modal image URL copied!');
    };
  }

  function navigateModal(direction) {
    if (modalActiveList.length <= 1) return;
    modalCurrentIndex = (modalCurrentIndex + direction + modalActiveList.length) % modalActiveList.length;
    renderModalContent();
  }

  function closeModal() {
    elements.imageModal.classList.remove('open');
  }

  /**
   * ZIP Export: Download a single product's images as a structured ZIP
   */
  async function downloadProductAsZip(prod) {
    const totalCount = (prod.model_photo ? 1 : 0) + prod.item_photos.length;
    if (totalCount === 0) {
      showToast('Error', 'No images found for this product.', 0, true);
      return;
    }

    const zip = new JSZip();
    const folderName = sanitizeFilename(prod.handle);
    const folder = zip.folder(folderName);

    showToast('Exporting ZIP', `Downloading 0 of ${totalCount} images...`, 5);

    let completed = 0;

    // 1. Download Model Photo
    if (prod.model_photo && prod.model_photo.url) {
      try {
        const blob = await fetchImageBlob(prod.model_photo.url);
        folder.file(`01_MODEL_PHOTO_${folderName}.jpg`, blob);
      } catch (err) {
        console.warn('Failed to fetch model photo:', err);
      }
      completed++;
      updateToastProgress(completed, totalCount);
    }

    // 2. Download Item Photos
    for (let i = 0; i < prod.item_photos.length; i++) {
      const item = prod.item_photos[i];
      try {
        const blob = await fetchImageBlob(item.url);
        const posTag = item.position && item.position !== 'Unpositioned' ? `POS_${item.position}` : `SHOT_${i + 2}`;
        folder.file(`02_ITEM_${posTag}_${i + 1}.jpg`, blob);
      } catch (err) {
        console.warn(`Failed to fetch item photo ${i}:`, err);
      }
      completed++;
      updateToastProgress(completed, totalCount);
    }

    showToast('Compressing ZIP', 'Creating zip archive...', 90);

    const zipBlob = await zip.generateAsync({ type: 'blob' });
    saveAs(zipBlob, `${folderName}_images.zip`);

    showToast('Download Complete', `Saved ${completed} images to ${folderName}_images.zip`, 100);
  }

  /**
   * ZIP Export: Selected photos only
   */
  async function downloadSelectedPhotosZip(prod) {
    const indices = Array.from(selectedSideIndices);
    const zip = new JSZip();
    const folderName = sanitizeFilename(prod.handle);
    const folder = zip.folder(`${folderName}_selected`);

    const totalCount = indices.length;
    showToast('Exporting Selected', `Downloading 0 of ${totalCount} images...`, 5);

    let completed = 0;
    for (let i = 0; i < indices.length; i++) {
      const idx = indices[i];
      const item = prod.item_photos[idx];
      if (item && item.url) {
        try {
          const blob = await fetchImageBlob(item.url);
          const posTag = item.position && item.position !== 'Unpositioned' ? `POS_${item.position}` : `SHOT_${idx + 2}`;
          folder.file(`ITEM_${posTag}_${idx + 1}.jpg`, blob);
        } catch (err) {
          console.warn(`Failed to fetch selected image ${idx}:`, err);
        }
        completed++;
        updateToastProgress(completed, totalCount);
      }
    }

    showToast('Compressing ZIP', 'Finalizing zip...', 90);
    const zipBlob = await zip.generateAsync({ type: 'blob' });
    saveAs(zipBlob, `${folderName}_selected_images.zip`);
    showToast('Complete', `Exported ${completed} selected images.`, 100);
  }

  /**
   * Batch Export: All 10 Products in a Master ZIP
   */
  async function batchExportAllProductsZip() {
    if (!products || products.length === 0) return;

    let totalImages = 0;
    products.forEach(p => {
      totalImages += (p.model_photo ? 1 : 0) + p.item_photos.length;
    });

    const confirmBatch = confirm(`Are you sure you want to download all ${products.length} products (${totalImages} total images) as a master ZIP?`);
    if (!confirmBatch) return;

    const zip = new JSZip();
    let currentImageIndex = 0;

    showToast('Batch Exporting', `Processing 0 of ${totalImages} total images...`, 2);

    for (let pIdx = 0; pIdx < products.length; pIdx++) {
      const prod = products[pIdx];
      const prodFolder = zip.folder(sanitizeFilename(prod.handle));

      // Model photo
      if (prod.model_photo && prod.model_photo.url) {
        try {
          const blob = await fetchImageBlob(prod.model_photo.url);
          prodFolder.file(`01_MODEL_PHOTO.jpg`, blob);
        } catch (e) {
          console.warn('Error fetching model photo for', prod.handle, e);
        }
        currentImageIndex++;
        updateToastProgress(currentImageIndex, totalImages);
      }

      // Item photos
      for (let i = 0; i < prod.item_photos.length; i++) {
        const item = prod.item_photos[i];
        try {
          const blob = await fetchImageBlob(item.url);
          const posTag = item.position && item.position !== 'Unpositioned' ? `pos_${item.position}` : `shot_${i + 2}`;
          prodFolder.file(`02_side_${posTag}.jpg`, blob);
        } catch (e) {
          console.warn('Error fetching item photo for', prod.handle, e);
        }
        currentImageIndex++;
        updateToastProgress(currentImageIndex, totalImages);
      }
    }

    showToast('Compressing Master Archive', 'Building complete ZIP catalog...', 95);
    const zipBlob = await zip.generateAsync({ type: 'blob' });
    saveAs(zipBlob, `Akeneo_Catalog_All_Images.zip`);
    showToast('Export Finished', `Successfully saved all ${currentImageIndex} images!`, 100);
  }

  /**
   * Fetch image as Blob with CORS handling
   */
  async function fetchImageBlob(url) {
    const response = await fetch(url, {
      mode: 'cors',
      cache: 'force-cache'
    });
    if (!response.ok) {
      throw new Error(`Failed to load ${url}: ${response.status}`);
    }
    return await response.blob();
  }

  /**
   * Download a single image directly via synthetic link
   */
  async function downloadSingleImage(url, filename) {
    try {
      showToast('Saving Image', 'Fetching image...', 30);
      const blob = await fetchImageBlob(url);
      saveAs(blob, filename);
      showToast('Saved', `Downloaded ${filename}`, 100);
    } catch (e) {
      console.warn('Direct fetch failed, falling back to open:', e);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.target = '_blank';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      hideToast();
    }
  }

  /**
   * Export CSV with Current Valid / Invalid Annotations
   */
  function exportValidatedCsv() {
    const exportRows = [];

    products.forEach(p => {
      // Row 1: Model Photo
      if (p.model_photo) {
        exportRows.push({
          'Handle': p.handle,
          'Title': p.title,
          'SKU': p.sku,
          'Vendor': p.vendor,
          'Type': p.type || p.category,
          'Image Role': 'Model Photo (Row 1)',
          'Image Position': p.model_photo.position,
          'Image Src': p.model_photo.url,
          'Validation Status': 'VALID_MODEL_HERO',
          'Is Side Image Invalid': 'NO',
          'Status': p.status
        });
      }

      // Rows 2+: Side Item Photos
      p.item_photos.forEach((item, i) => {
        exportRows.push({
          'Handle': p.handle,
          'Title': '',
          'SKU': '',
          'Vendor': '',
          'Type': '',
          'Image Role': 'Item Side Photo (Row 2+)',
          'Image Position': item.position,
          'Image Src': item.url,
          'Validation Status': item.isValid ? 'VALID_SIDE_IMAGE' : 'INVALID_SIDE_IMAGE',
          'Is Side Image Invalid': item.isValid ? 'NO' : 'YES',
          'Status': ''
        });
      });
    });

    const csvString = Papa.unparse(exportRows);
    const blob = new Blob([csvString], { type: 'text/csv;charset=utf-8;' });
    saveAs(blob, 'Validated_Side_Images_Export.csv');
    showToast('CSV Exported', `Generated Validated_Side_Images_Export.csv (${exportRows.length} rows)`, 100);
  }

  /**
   * Real-time Qwen Vision Verification using Server-Sent Events (SSE)
   */
  function startAiStreamVerification(targetHandle = null) {
    const isSingle = Boolean(targetHandle);
    const url = isSingle
      ? `/api/stream_verify?handle=${encodeURIComponent(targetHandle)}`
      : `/api/stream_verify`;

    showToast(
      'Qwen Vision Inspection',
      isSingle ? `Connecting to Qwen Vision for ${targetHandle}...` : 'Starting Qwen Vision batch verification...',
      5
    );

    let eventSource;
    try {
      eventSource = new EventSource(url);
    } catch (err) {
      console.warn('EventSource failed, falling back to local ai_results.json', err);
      fallbackLoadAiResults();
      return;
    }

    eventSource.addEventListener('start', (e) => {
      try {
        const data = JSON.parse(e.data);
        showToast('Qwen Vision Active', `Inspecting ${data.total_photos} photos with Qwen 3.8 Flash...`, 10);
      } catch (err) {}
    });

    eventSource.addEventListener('checking', (e) => {
      try {
        const data = JSON.parse(e.data);
        const pct = Math.max(10, Math.min(95, Math.round((data.current / data.total) * 100)));
        showToast(
          'Qwen Vision Analyzing',
          `[${data.current}/${data.total}] Analyzing Pos #${data.position || 'Side'}...`,
          pct
        );
      } catch (err) {}
    });

    eventSource.addEventListener('photo_result', (e) => {
      try {
        const data = JSON.parse(e.data);
        const { handle, position, result, current, total } = data;

        // Update product in memory
        const prod = products.find(p => p.handle === handle || p.title === handle);
        if (prod && prod.item_photos) {
          const photo = prod.item_photos.find(p => String(p.position) === String(position)) ||
                        prod.item_photos.find(p => p.url === result.url);
          if (photo) {
            photo.ai_score = result.score;
            photo.ai_status = result.status;
            photo.ai_reason = result.reason;
            photo.isValid = result.is_valid;
            photo.verdict = result.verdict;
          }
        }

        // Live re-render if viewing this product
        const curProd = products[currentProductIndex];
        if (curProd && (curProd.handle === handle || curProd.title === handle)) {
          renderSideImages(curProd);
        }
        renderSidebar();

        const pct = Math.max(10, Math.min(95, Math.round((current / total) * 100)));
        const statusLabel = result.status === 'match' ? 'MATCH ✓' : (result.verdict === 'review' ? 'REVIEW 👁' : 'MISMATCH ⚠');
        showToast(
          `Qwen: ${statusLabel}`,
          `[${current}/${total}] ${result.reason || ''}`,
          pct
        );
      } catch (err) {
        console.error('Error handling SSE result:', err);
      }
    });

    eventSource.addEventListener('complete', (e) => {
      eventSource.close();
      let msg = 'Qwen Vision verification completed!';
      try {
        const data = JSON.parse(e.data);
        if (data.message) msg = data.message;
      } catch (err) {}

      showToast('Verification Complete', msg, 100);
      renderApp();
    });

    eventSource.onerror = (err) => {
      console.warn('EventSource connection error, checking local fallback:', err);
      eventSource.close();
      fallbackLoadAiResults();
    };
  }

  async function fallbackLoadAiResults() {
    try {
      const resp = await fetch('ai_results.json?t=' + Date.now());
      if (resp.ok) {
        const aiData = await resp.json();
        const stats = applyAiDataToProducts(aiData);
        renderApp();
        showToast('AI Results Applied', `Loaded: ${stats.matchedCount} valid matches, ${stats.mismatchCount} mismatches flagged.`, 100);
      } else {
        showToast('Notice', 'Completed checking.', 100);
      }
    } catch (e) {
      showToast('Notice', 'Completed checking.', 100);
    }
  }

  /**
   * Save human operator override to Obsidian Knowledge Base (Active Learning)
   */
  async function saveFeedbackToKnowledgeBase(handle, url, position, isValid) {
    try {
      await fetch('/api/save_feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          handle: handle,
          url: url,
          position: position,
          is_valid: isValid,
          note: isValid ? 'Manual Valid by Operator' : 'Manual Invalid by Operator'
        })
      });
    } catch (e) {
      console.warn('Could not sync feedback to Knowledge Base:', e);
    }
  }

  /**
   * Toast helper functions
   */
  function showToast(title, subtitle, percent = 0, isError = false) {
    elements.toastTitle.textContent = title;
    elements.toastSubtitle.textContent = subtitle;
    elements.toastProgressFill.style.width = `${percent}%`;
    elements.progressToast.classList.add('visible');

    if (isError) {
      elements.toastProgressFill.style.backgroundColor = '#EF4444';
      elements.toastSpinner.style.display = 'none';
    } else {
      elements.toastProgressFill.style.backgroundColor = '#6C5CE7';
      elements.toastSpinner.style.display = percent >= 100 ? 'none' : 'block';
    }

    if (percent >= 100 || isError) {
      setTimeout(() => {
        elements.progressToast.classList.remove('visible');
      }, 2500);
    }
  }

  function updateToastProgress(current, total) {
    const pct = Math.round((current / total) * 90);
    elements.toastSubtitle.textContent = `Downloading ${current} of ${total} images...`;
    elements.toastProgressFill.style.width = `${pct}%`;
  }

  function hideToast() {
    elements.progressToast.classList.remove('visible');
  }

  /**
   * Clipboard Helper
   */
  function copyToClipboard(text, successMsg = 'Copied to clipboard!') {
    navigator.clipboard.writeText(text).then(() => {
      showToast('Copied', successMsg, 100);
    }).catch(() => {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      showToast('Copied', successMsg, 100);
    });
  }

  /**
   * Dark / Light Theme
   */
  function initTheme() {
    const saved = localStorage.getItem('akeneo_theme');
    if (saved === 'dark') {
      document.body.classList.add('dark-theme');
      elements.themeToggleBtn.innerHTML = '<i class="fa-solid fa-sun"></i>';
    }
  }

  function toggleTheme() {
    const isDark = document.body.classList.toggle('dark-theme');
    localStorage.setItem('akeneo_theme', isDark ? 'dark' : 'light');
    elements.themeToggleBtn.innerHTML = isDark 
      ? '<i class="fa-solid fa-sun"></i>' 
      : '<i class="fa-solid fa-moon"></i>';
  }

  /**
   * Utilities
   */
  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function sanitizeFilename(name) {
    return (name || 'product').replace(/[^a-zA-Z0-9_-]/g, '_').replace(/_+/g, '_');
  }

  // Start app on DOMContentLoaded
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
