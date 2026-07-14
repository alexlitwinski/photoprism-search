class PhotoPrismSearchCard extends HTMLElement {
  // Set up properties
  static get properties() {
    return {
      hass: {},
      config: {}
    };
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.photos = [];
    this.searching = false;
    this.error = null;
    this.translatedQuery = '';
    this.selectedPhoto = null; // For the forward dialog
  }

  // Set configuration
  setConfig(config) {
    this.config = config;
  }

  // Home Assistant sets the hass object whenever state changes
  set hass(hass) {
    this._hass = hass;
    this.render();
  }

  get hass() {
    return this._hass;
  }

  // First time the element is added to DOM
  connectedCallback() {
    this.render();
  }

  // Helper to discover the photoprism_search entry_id
  async getEntryId() {
    if (this.config && this.config.entry_id) {
      return this.config.entry_id;
    }
    
    // Dynamically retrieve entry_id from Home Assistant
    try {
      const entries = await this.hass.callWS({
        type: 'config_entries/get',
        domain: 'photoprism_search'
      });
      if (entries && entries.length > 0) {
        return entries[0].entry_id;
      }
    } catch (e) {
      console.error("Failed to fetch photoprism_search entries via WebSocket", e);
    }
    return null;
  }

  // Handle the search action
  async performSearch() {
    const queryInput = this.shadowRoot.querySelector('#search-input');
    const query = queryInput ? queryInput.value.trim() : '';
    if (!query) return;

    this.searching = true;
    this.error = null;
    this.photos = [];
    this.translatedQuery = '';
    this.render();

    const entryId = await this.getEntryId();
    if (!entryId) {
      this.searching = false;
      this.error = 'Nenhuma configuração do PhotoPrism AI Search encontrada. Configure a integração primeiro.';
      this.render();
      return;
    }

    try {
      const result = await this.hass.callWS({
        type: 'photoprism_search/search',
        entry_id: entryId,
        query: query
      });

      this.photos = result.photos || [];
      this.translatedQuery = result.translated_query || '';
      if (this.photos.length === 0) {
        this.error = 'Nenhuma foto encontrada para a busca realizada.';
      }
    } catch (err) {
      console.error(err);
      this.error = err.message || 'Erro ao realizar a busca.';
    } finally {
      this.searching = false;
      this.render();
    }
  }

  // Open the Forward/Share dialog
  openForwardDialog(photo) {
    this.selectedPhoto = photo;
    this.render();
    const dialog = this.shadowRoot.querySelector('#forward-dialog');
    if (dialog) dialog.style.display = 'flex';
  }

  // Close the Forward/Share dialog
  closeForwardDialog() {
    this.selectedPhoto = null;
    const dialog = this.shadowRoot.querySelector('#forward-dialog');
    if (dialog) dialog.style.display = 'none';
  }

  // Execute forwarding of a photo
  async sendForward() {
    const notifySelect = this.shadowRoot.querySelector('#notify-select');
    const customNotify = this.shadowRoot.querySelector('#custom-notify-input');
    
    let notifyTarget = notifySelect ? notifySelect.value : '';
    if (customNotify && customNotify.value.trim()) {
      notifyTarget = customNotify.value.trim();
    }

    if (!notifyTarget || !this.selectedPhoto) return;

    const [domain, service] = notifyTarget.includes('.') 
      ? notifyTarget.split('.') 
      : ['notify', notifyTarget];

    try {
      // PhotoPrism needs full URL for external notification if not proxied
      // We pass the local proxy URL or the direct download URL
      // If the notifier needs public access, it might require a public URL. 
      // Home Assistant handles local proxy urls (/api/...) well for internal notifications (companion app).
      const imageUrl = window.location.origin + this.selectedPhoto.thumb_url;

      await this.hass.callService(domain, service, {
        title: this.selectedPhoto.title,
        message: `Foto enviada via PhotoPrism AI Search.\nData: ${this.selectedPhoto.taken_at || 'Desconhecida'}\nLocal: ${this.selectedPhoto.place || 'Desconhecido'}`,
        data: {
          image: imageUrl,
          clickAction: imageUrl
        }
      });
      
      alert(`Foto enviada com sucesso para ${notifyTarget}!`);
      this.closeForwardDialog();
    } catch (err) {
      console.error(err);
      alert(`Erro ao encaminhar: ${err.message || err}`);
    }
  }

  // Main rendering method
  render() {
    if (!this.hass) return;

    // Save input value and selection/focus state
    const activeEl = this.shadowRoot.activeElement;
    const inputWasFocused = activeEl && activeEl.id === 'search-input';
    const oldInputValue = this.shadowRoot.querySelector('#search-input') 
      ? this.shadowRoot.querySelector('#search-input').value 
      : '';
      
    const notifyInputWasFocused = activeEl && activeEl.id === 'custom-notify-input';
    const oldNotifyValue = this.shadowRoot.querySelector('#custom-notify-input')
      ? this.shadowRoot.querySelector('#custom-notify-input').value
      : '';


    // Get notify services dynamically from hass
    const notifyServices = [];
    if (this.hass.services && this.hass.services.notify) {
      Object.keys(this.hass.services.notify).forEach(service => {
        notifyServices.push(`notify.${service}`);
      });
    }

    const style = `
      <style>
        :host {
          display: block;
        }
        
        .card-container {
          background: var(--ha-card-background, rgba(20, 20, 25, 0.65));
          backdrop-filter: blur(16px);
          -webkit-backdrop-filter: blur(16px);
          border: 1px solid rgba(255, 255, 255, 0.1);
          border-radius: var(--ha-card-border-radius, 12px);
          padding: 16px;
          color: var(--primary-text-color, #ffffff);
          box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
          transition: all 0.3s ease;
          overflow: hidden;
        }

        .header {
          display: flex;
          align-items: center;
          gap: 10px;
          margin-bottom: 16px;
        }

        .header ha-icon {
          color: var(--accent-color, #ff9800);
          --mdc-icon-size: 28px;
        }

        .title {
          font-size: 20px;
          font-weight: 600;
          letter-spacing: 0.5px;
          background: linear-gradient(45deg, #ff9800, #ff5722);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }

        .search-box {
          display: flex;
          gap: 8px;
          margin-bottom: 16px;
        }

        .search-box input {
          flex: 1;
          background: rgba(255, 255, 255, 0.05);
          border: 1px solid rgba(255, 255, 255, 0.15);
          border-radius: 8px;
          padding: 12px 16px;
          color: white;
          font-size: 14px;
          outline: none;
          transition: all 0.3s ease;
        }

        .search-box input:focus {
          border-color: var(--accent-color, #ff9800);
          background: rgba(255, 255, 255, 0.08);
          box-shadow: 0 0 8px rgba(255, 152, 0, 0.2);
        }

        .search-box button {
          background: linear-gradient(135deg, var(--accent-color, #ff9800) 0%, #ff5722 100%);
          border: none;
          border-radius: 8px;
          color: white;
          padding: 0 20px;
          font-size: 14px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.2s ease;
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .search-box button:hover {
          transform: translateY(-1px);
          box-shadow: 0 4px 12px rgba(255, 87, 34, 0.3);
        }

        .search-box button:active {
          transform: translateY(1px);
        }

        .translated-info {
          font-size: 11px;
          color: var(--secondary-text-color, #aaaaaa);
          margin-top: -10px;
          margin-bottom: 14px;
          font-style: italic;
          padding-left: 4px;
        }

        .loader-bar {
          height: 3px;
          width: 100%;
          background: rgba(255, 255, 255, 0.05);
          border-radius: 2px;
          overflow: hidden;
          margin-bottom: 16px;
          display: none;
        }

        .loader-progress {
          height: 100%;
          width: 50%;
          background: linear-gradient(90deg, #ff9800, #ff5722);
          border-radius: 2px;
          animation: loading-anim 1.5s infinite ease-in-out;
        }

        @keyframes loading-anim {
          0% { margin-left: -50%; }
          100% { margin-left: 100%; }
        }

        .photo-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
          gap: 12px;
        }

        .photo-item {
          position: relative;
          aspect-ratio: 1;
          border-radius: 8px;
          overflow: hidden;
          background: rgba(0,0,0,0.2);
          border: 1px solid rgba(255, 255, 255, 0.05);
          transition: all 0.3s cubic-bezier(0.165, 0.84, 0.44, 1);
        }

        .photo-item:hover {
          transform: scale(1.04);
          box-shadow: 0 8px 16px rgba(0,0,0,0.5);
          border-color: rgba(255, 152, 0, 0.4);
        }

        .photo-item img {
          width: 100%;
          height: 100%;
          object-fit: cover;
        }

        .photo-overlay {
          position: absolute;
          inset: 0;
          background: linear-gradient(to top, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.2) 60%, rgba(0,0,0,0) 100%);
          opacity: 0;
          transition: opacity 0.25s ease;
          display: flex;
          flex-direction: column;
          justify-content: flex-end;
          padding: 8px;
        }

        .photo-item:hover .photo-overlay {
          opacity: 1;
        }

        .photo-title {
          font-size: 12px;
          font-weight: 600;
          margin-bottom: 2px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .photo-meta {
          font-size: 9px;
          color: #cccccc;
          display: flex;
          flex-direction: column;
          gap: 1px;
        }

        .actions {
          display: flex;
          gap: 6px;
          margin-top: 6px;
        }

        .action-btn {
          flex: 1;
          background: rgba(255,255,255,0.15);
          border: none;
          color: white;
          font-size: 9px;
          padding: 4px 6px;
          border-radius: 4px;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 4px;
          font-weight: 600;
          transition: background 0.2s;
        }

        .action-btn:hover {
          background: var(--accent-color, #ff9800);
        }

        .error-message {
          color: #f44336;
          font-size: 13px;
          background: rgba(244, 67, 54, 0.1);
          border-left: 4px solid #f44336;
          padding: 10px;
          border-radius: 4px;
          margin-bottom: 16px;
        }

        /* Modal dialog styles */
        .dialog-overlay {
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: rgba(0, 0, 0, 0.7);
          backdrop-filter: blur(8px);
          -webkit-backdrop-filter: blur(8px);
          display: none;
          align-items: center;
          justify-content: center;
          z-index: 1000;
          animation: fade-in 0.25s ease;
        }

        @keyframes fade-in {
          from { opacity: 0; }
          to { opacity: 1; }
        }

        .dialog-card {
          background: var(--ha-card-background, #1c1c22);
          border: 1px solid rgba(255,255,255,0.1);
          border-radius: 12px;
          width: 90%;
          max-width: 400px;
          padding: 20px;
          color: white;
          box-shadow: 0 10px 25px rgba(0,0,0,0.5);
        }

        .dialog-title {
          font-size: 16px;
          font-weight: 600;
          margin-bottom: 14px;
        }

        .dialog-fields {
          display: flex;
          flex-direction: column;
          gap: 12px;
          margin-bottom: 20px;
        }

        .dialog-fields select, .dialog-fields input {
          background: rgba(255,255,255,0.05);
          border: 1px solid rgba(255,255,255,0.15);
          border-radius: 6px;
          padding: 10px;
          color: white;
          font-size: 13px;
          outline: none;
        }

        .dialog-fields select option {
          background: #1c1c22;
          color: white;
        }

        .dialog-buttons {
          display: flex;
          justify-content: flex-end;
          gap: 10px;
        }

        .btn-cancel {
          background: transparent;
          border: 1px solid rgba(255,255,255,0.2);
          color: white;
          padding: 8px 16px;
          border-radius: 6px;
          cursor: pointer;
          font-size: 13px;
        }

        .btn-send {
          background: linear-gradient(135deg, var(--accent-color, #ff9800) 0%, #ff5722 100%);
          border: none;
          color: white;
          padding: 8px 16px;
          border-radius: 6px;
          cursor: pointer;
          font-size: 13px;
          font-weight: 600;
        }
      </style>
    `;

    let photosHtml = '';
    this.photos.forEach(photo => {
      const formattedDate = photo.taken_at ? new Date(photo.taken_at).toLocaleDateString() : '';
      const labelsJoined = photo.labels ? photo.labels.slice(0, 3).join(', ') : '';
      
      photosHtml += `
        <div class="photo-item">
          <img src="${photo.thumb_url}" alt="${photo.title}" loading="lazy" />
          <div class="photo-overlay">
            <div class="photo-title">${photo.title}</div>
            <div class="photo-meta">
              ${formattedDate ? `<span>📅 ${formattedDate}</span>` : ''}
              ${photo.place ? `<span>📍 ${photo.place}</span>` : ''}
              ${labelsJoined ? `<span>🏷️ ${labelsJoined}</span>` : ''}
            </div>
            <div class="actions">
              <button class="action-btn" onclick="this.getRootNode().host.openForwardDialog(${JSON.stringify(photo).replace(/"/g, '&quot;')})">
                <ha-icon icon="mdi:share-variant"></ha-icon> Encaminhar
              </button>
              <a href="${photo.download_url}" target="_blank" style="text-decoration: none; flex: 1;">
                <button class="action-btn" style="width: 100%;">
                  <ha-icon icon="mdi:download"></ha-icon> Baixar
                </button>
              </a>
            </div>
          </div>
        </div>
      `;
    });

    // Notify services dropdown options
    let notifyOptions = '<option value="">Selecione um serviço...</option>';
    notifyServices.forEach(srv => {
      notifyOptions += `<option value="${srv}">${srv}</option>`;
    });

    const body = `
      <div class="card-container">
        <div class="header">
          <ha-icon icon="mdi:image-search-outline"></ha-icon>
          <span class="title">PhotoPrism AI Search</span>
        </div>

        <div class="search-box">
          <input 
            type="text" 
            id="search-input" 
            placeholder="Ex: Hanna na praia com o Alex em 2024..." 
            onkeydown="if(event.key === 'Enter') this.getRootNode().host.performSearch()"
          />
          <button onclick="this.getRootNode().host.performSearch()">
            <ha-icon icon="mdi:magnify"></ha-icon> Buscar
          </button>
        </div>

        ${this.translatedQuery ? `<div class="translated-info">Filtro gerado: "${this.translatedQuery}"</div>` : ''}

        <div class="loader-bar" style="display: ${this.searching ? 'block' : 'none'};">
          <div class="loader-progress"></div>
        </div>

        ${this.error ? `<div class="error-message">${this.error}</div>` : ''}

        <div class="photo-grid">
          ${photosHtml}
        </div>
      </div>

      <!-- Share Dialog -->
      <div id="forward-dialog" class="dialog-overlay">
        <div class="dialog-card">
          <div class="dialog-title">Encaminhar Foto</div>
          <div class="dialog-fields">
            <label style="font-size: 12px; color: #aaa;">Enviar para serviço de notificação:</label>
            <select id="notify-select">
              ${notifyOptions}
            </select>
            <span style="font-size: 11px; color: #888; text-align: center;">ou digite um serviço customizado:</span>
            <input type="text" id="custom-notify-input" placeholder="notify.telegram_bot" />
          </div>
          <div class="dialog-buttons">
            <button class="btn-cancel" onclick="this.getRootNode().host.closeForwardDialog()">Cancelar</button>
            <button class="btn-send" onclick="this.getRootNode().host.sendForward()">Enviar</button>
          </div>
        </div>
      </div>
    `;

    this.shadowRoot.innerHTML = style + body;

    // Restore input value and focus
    const newInput = this.shadowRoot.querySelector('#search-input');
    if (newInput) {
      newInput.value = oldInputValue;
      if (inputWasFocused) {
        newInput.focus();
        // Place cursor at the end of the text
        newInput.setSelectionRange(oldInputValue.length, oldInputValue.length);
      }
    }

    const newNotifyInput = this.shadowRoot.querySelector('#custom-notify-input');
    if (newNotifyInput) {
      newNotifyInput.value = oldNotifyValue;
      if (notifyInputWasFocused) {
        newNotifyInput.focus();
        newNotifyInput.setSelectionRange(oldNotifyValue.length, oldNotifyValue.length);
      }
    }
  }
}

customElements.define('photoprism-search-card', PhotoPrismSearchCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "photoprism-search-card",
  name: "PhotoPrism AI Search",
  description: "Buscas inteligentes com IA em sua base do PhotoPrism",
  preview: true
});
