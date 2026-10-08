/* Fautexprimer — frontend natif, sans framework ni étape de compilation. */
(() => {
  'use strict'

  const DEFAULT_LIMITS = { name: 'Dane', maxChars: 20_000, maxWords: 3_500, maxSentences: 300 }
  const $ = (id) => document.getElementById(id)

  const dom = {
    loading: $('studio-loading'), studio: $('studio'), vitrine: $('vitrine'), historyPage: $('history-page'),
    historyStats: $('history-stats'), synthesesList: $('syntheses-list'), voicesList: $('voices-list'),
    historyRefresh: $('history-refresh'), clearSyntheses: $('clear-syntheses'), clearVoices: $('clear-voices'),
    providerPills: $('provider-pills'), providerNote: $('provider-note'), geminiField: $('gemini-field'), geminiVoice: $('gemini-voice'),
    edgeLoading: $('edge-loading'), edgeSearchField: $('edge-search-field'), edgeSearch: $('edge-search'),
    edgeField: $('edge-field'), edgeVoice: $('edge-voice'), edgeCount: $('edge-count'),
    mimoDescription: $('mimo-description'), mimoPanel: $('mimo-panel'), refFile: $('ref-file'),
    recordButton: $('record-button'), clipReady: $('clip-ready'), speed: $('speed'), gain: $('gain'),
    speedLabel: $('speed-label'), gainLabel: $('gain-label'), formatOptions: $('format-options'),
    formatSummary: $('format-summary'), text: $('text'),
    textCounter: $('text-counter'), textChars: $('text-chars'), textWords: $('text-words'),
    textSentences: $('text-sentences'), textLimit: $('text-limit'), limitMessage: $('limit-message'),
    synthesize: $('synthesize'),
    status: $('status'), busy: $('synthesis-busy'), wave: $('wave'), result: $('audio-result'),
    audio: $('audio-player'), download: $('download'), healthDot: $('health-dot'),
    healthText: $('health-text'), models: $('models'),
  }

  const state = {
    config: null, provider: 'edge', geminiVoice: '', edgeVoice: '', edgeVoices: [],
    voicesLoading: true, edgeSearch: '', speed: 1, gain: 1, formats: ['.wav'],
    refVoiceId: null, mimoUrl: '', mimoModel: '', busy: false, audioUrl: '',
    history: { stats: null, syntheses: [], voices: [] }, historyLoading: false,
    recorder: null, recordStream: null, recordChunks: [], recordTimer: null,
    recordAutoStop: null, recordSeconds: 0,
  }

  async function apiGet(path) {
    const response = await fetch(path)
    if (!response.ok) throw new Error(`GET ${path} : HTTP ${response.status}`)
    return response.json()
  }

  function errorText(data, status) {
    const detail = data && data.detail
    if (typeof detail === 'string' && detail) return detail
    if (Array.isArray(detail)) return detail.map((item) => item?.msg || JSON.stringify(item)).join(' ; ')
    if (detail) return JSON.stringify(detail)
    return `HTTP ${status}`
  }

  async function responseData(response) {
    try { return await response.json() } catch { return null }
  }

  async function apiSend(path, method = 'GET', body = undefined) {
    const response = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const data = await responseData(response)
    if (!response.ok) throw new Error(errorText(data, response.status))
    return data
  }

  function setStatus(kind, message = '') {
    dom.status.hidden = !message
    dom.status.className = `status ${kind}`
    dom.status.textContent = message
  }

  function element(tag, className, text) {
    const node = document.createElement(tag)
    if (className) node.className = className
    if (text !== undefined) node.textContent = text
    return node
  }

  function encodeWav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2)
    const view = new DataView(buffer)
    const writeString = (offset, value) => {
      for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i))
    }
    writeString(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true)
    writeString(8, 'WAVE'); writeString(12, 'fmt ')
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true)
    view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true)
    view.setUint16(32, 2, true); view.setUint16(34, 16, true)
    writeString(36, 'data'); view.setUint32(40, samples.length * 2, true)
    let offset = 44
    for (const sample of samples) {
      const value = Math.max(-1, Math.min(1, sample))
      view.setInt16(offset, value < 0 ? value * 0x8000 : value * 0x7fff, true)
      offset += 2
    }
    return new Blob([view], { type: 'audio/wav' })
  }

  async function blobToWav(blob, targetRate = 24000) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext
    if (!AudioContextClass) throw new Error('AudioContext indisponible')
    const context = new AudioContextClass()
    let decoded
    try { decoded = await context.decodeAudioData(await blob.arrayBuffer()) }
    finally { await context.close() }

    const length = decoded.length
    const mono = new Float32Array(length)
    for (let channel = 0; channel < decoded.numberOfChannels; channel += 1) {
      const data = decoded.getChannelData(channel)
      for (let i = 0; i < length; i += 1) mono[i] += data[i] / decoded.numberOfChannels
    }
    if (decoded.sampleRate === targetRate) return encodeWav(mono, targetRate)

    const outputLength = Math.max(1, Math.round(length * targetRate / decoded.sampleRate))
    const output = new Float32Array(outputLength)
    for (let i = 0; i < outputLength; i += 1) {
      const position = i * (length - 1) / Math.max(1, outputLength - 1)
      const low = Math.floor(position)
      const high = Math.min(low + 1, length - 1)
      const fraction = position - low
      output[i] = mono[low] * (1 - fraction) + mono[high] * fraction
    }
    return encodeWav(output, targetRate)
  }

  const MODELS = [
    {
      id: 'gemini', name: 'Eben', color: 'blue', tagline: 'Voix cloud, expressive et naturelle', capacity: '8 000 car. / 1 400 mots / 200 phrases',
      description: "Eben genere une voix tres naturelle, avec une intonation riche et des styles varies. C'est le moteur a privilegier pour du contenu soigne : narration, publicite, podcast.",
      voices: '30 voix predefinies', speed: '0.5x – 2.0x', cloning: false, needsKey: "Cle d'acces",
      strengths: ['30 voix aux tonalites distinctes (Bright, Warm, Gravelly…)', 'Qualite de voix la plus naturelle', 'Controle des styles et de la personnalite'],
      limits: ["Necesite une cle d'acces personnel", 'Plus lent sur les longs textes', 'Export MP3/OGG/FLAC/M4A et vitesse : ffmpeg requis'],
      bestFor: 'Narration, contenus premium, voix caracterisees',
    },
    {
      id: 'edge', name: 'Dane', color: 'green', tagline: 'Le multilingue gratuit, sans cle', capacity: '20 000 car. / 3 500 mots / 600 phrases',
      description: "Dane propose des voix neuronales et n'a besoin d'aucune cle d'acces. Il couvre plus de 300 voix dans 75 langues : ideal pour changer de langue ou rester gratuit.",
      voices: '322 voix, 75 langues', speed: '0.5x – 2.0x', cloning: false, needsKey: 'Aucune',
      strengths: ['322 voix, 140 locales, 75 langues', "Aucune cle d'acces — immediatement utilisable", 'Export MP3 natif, sans ffmpeg'],
      limits: ['Qualite un peu plus « standard » que le cloud', 'Style et emotion moins controlables'],
      bestFor: 'Tests rapides, autres langues, usage sans configuration',
    },
    {
      id: 'mimo', name: 'Michel', color: 'violet', tagline: 'Votre propre voix, clonee', capacity: '4 000 car. / 700 mots / 120 phrases',
      description: "Michel reproduit une voix a partir d'un court extrait audio. Il lit ensuite n'importe quel texte avec cette voix : la votre, ou une voix que vous fournissez.",
      voices: '1 voix clonee (la votre)', speed: '0.5x – 2.0x', cloning: true, needsKey: 'Configuration requise',
      strengths: ["Clonage de voix a partir d'un clip de quelques secondes", 'Enregistrement du clip au micro, directement dans le navigateur', "Votre voix lit n'importe quel texte"],
      limits: ['Un clip de reference (WAV/MP3) est toujours requis', 'Ne cree aucune voix : il en clone une', "Depend d'un service distant (cle/quota selon la configuration)"],
      bestFor: 'Contenus avec votre voix, doublage, personnalisation',
    },
  ]

  function makeModelCard(model) {
    const card = element('article', `model-card ${model.color}`)
    const top = element('div', 'model-top')
    top.append(element('h2', '', model.name))
    card.append(top, element('p', 'tagline', model.tagline), element('p', 'desc', model.description))

    const facts = element('dl', 'facts')
    const factValues = [
      ['Voix', model.voices], ['Vitesse', model.speed], ['Clonage', model.cloning ? 'Oui' : 'Non'],
      ['Cle requise', model.needsKey || 'Aucune'], ['Plafond', model.capacity],
    ]
    for (const [label, value] of factValues) {
      const item = element('div')
      item.append(element('dt', '', label), element('dd', '', value))
      facts.append(item)
    }
    card.append(facts)

    const columns = element('div', 'model-cols')
    for (const [title, items, className] of [['Points forts', model.strengths, 'pro'], ['A savoir', model.limits, 'con']]) {
      const column = element('div')
      column.append(element('h3', '', title))
      const list = element('ul', className)
      items.forEach((item) => list.append(element('li', '', item)))
      column.append(list)
      columns.append(column)
    }
    card.append(columns)
    const bestFor = element('p', 'bestfor')
    bestFor.append(element('strong', '', 'Ideal pour : '), document.createTextNode(model.bestFor))
    card.append(bestFor)
    return card
  }

  function renderVitrine() {
    dom.models.replaceChildren(...MODELS.map(makeModelCard))
  }

  function providerName(id, fallback) {
    return state.config?.providers?.find((provider) => provider.id === id)?.name || fallback
  }

  function renderProviders() {
    dom.providerPills.replaceChildren()
    for (const provider of state.config.providers) {
      const button = element('button', 'pill', provider.name)
      button.type = 'button'
      button.title = provider.hint
      button.setAttribute('role', 'tab')
      button.dataset.provider = provider.id
      button.addEventListener('click', () => {
        state.provider = provider.id
        renderProvider()
      })
      dom.providerPills.append(button)
    }
  }

  function renderGeminiVoices() {
    dom.geminiVoice.replaceChildren()
    for (const voice of state.config.geminiVoices) {
      const option = element('option', '', `${voice.name} — ${voice.tone}`)
      option.value = voice.name
      dom.geminiVoice.append(option)
    }
    dom.geminiVoice.value = state.geminiVoice
    $('gemini-label').textContent = `Voix ${providerName('gemini', 'Eben')}`
  }

  function filteredEdgeVoices() {
    const needle = state.edgeSearch.trim().toLocaleLowerCase('fr')
    if (!needle) return state.edgeVoices
    return state.edgeVoices.filter((voice) =>
      `${voice.label} ${voice.localeName} ${voice.shortName} ${voice.gender} ${voice.categories} ${voice.personalities}`
        .toLocaleLowerCase('fr').includes(needle),
    )
  }

  function renderEdgeVoices() {
    const filtered = filteredEdgeVoices()
    let options = filtered
    const current = state.edgeVoices.find((voice) => voice.shortName === state.edgeVoice)
    if (current && !filtered.some((voice) => voice.shortName === state.edgeVoice)) options = [current, ...filtered]
    dom.edgeVoice.replaceChildren()
    for (const voice of options) {
      const option = element('option', '', `${voice.label} — ${voice.localeName}`)
      option.value = voice.shortName
      dom.edgeVoice.append(option)
    }
    dom.edgeVoice.value = state.edgeVoice
    dom.edgeCount.textContent = `Rechercher (${filtered.length} voix)`
    $('edge-label').textContent = `Voix ${providerName('edge', 'Dane')}`
  }

  function selectedFormats() {
    return [...dom.formatOptions.querySelectorAll('input:checked')].map((input) => input.value)
  }

  function updateFormatSummary() {
    const formats = selectedFormats()
    dom.formatSummary.textContent = formats.length > 1
      ? `${formats.length} formats sélectionnés · téléchargement ZIP`
      : `${formats.length} format sélectionné`
    dom.synthesize.disabled = state.busy || !formats.length || updateTextState.over
  }

  function renderFormats() {
    dom.formatOptions.replaceChildren()
    for (const format of state.config.formats) {
      const label = element('label', 'format-check')
      const input = document.createElement('input')
      input.type = 'checkbox'
      input.value = format.ext
      input.checked = state.formats.includes(format.ext)
      input.addEventListener('change', updateFormatSummary)
      const text = element('span', '', format.ext.replace('.', '').toUpperCase())
      label.append(input, text)
      dom.formatOptions.append(label)
    }
    updateFormatSummary()
  }

  function currentLimits() {
    return state.config?.textLimits?.[state.provider] || DEFAULT_LIMITS
  }

  function textMetrics(value) {
    const words = value.match(/[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*/gu) || []
    const sentences = value.match(/[.!?…]+/g)?.length || (value.trim() ? 1 : 0)
    return { chars: Array.from(value).length, words: words.length, sentences }
  }

  function formatNumber(value) {
    return Number(value || 0).toLocaleString('fr-FR')
  }

  function renderProvider() {
    for (const button of dom.providerPills.querySelectorAll('[data-provider]')) {
      const active = button.dataset.provider === state.provider
      button.classList.toggle('active', active)
      button.setAttribute('aria-selected', String(active))
    }
    const isGemini = state.provider === 'gemini'
    const isEdge = state.provider === 'edge'
    const isMimo = state.provider === 'mimo'
    const provider = state.config?.providers?.find((item) => item.id === state.provider)
    const limits = currentLimits()
    dom.providerNote.textContent = provider
      ? `${provider.name} · plafond prudent : ${formatNumber(limits.maxChars)} caracteres · ${formatNumber(limits.maxWords)} mots · ${formatNumber(limits.maxSentences)} phrases`
      : ''
    dom.textLimit.textContent = formatNumber(limits.maxChars)
    dom.geminiField.hidden = !isGemini
    dom.edgeLoading.hidden = !(isEdge && state.voicesLoading)
    dom.edgeSearchField.hidden = !(isEdge && !state.voicesLoading)
    dom.edgeField.hidden = !(isEdge && !state.voicesLoading)
    dom.mimoDescription.hidden = !isMimo
    dom.mimoPanel.hidden = !isMimo
    if (isEdge && !state.voicesLoading) renderEdgeVoices()
    updateTextState()
  }

  function formatDate(value) {
    const date = new Date(value)
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString('fr-FR', {
      day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    })
  }

  function formatBytes(value) {
    const bytes = Number(value) || 0
    if (bytes < 1024) return `${bytes} o`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} Ko`
    return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`
  }

  function historyAction(label, className, handler) {
    const button = element('button', className, label)
    button.type = 'button'
    button.addEventListener('click', handler)
    return button
  }

  function renderHistoryStats(stats) {
    const values = [
      ['Syntheses', stats.syntheses], ['Voix', stats.voices],
      ['Audio genere', formatBytes(stats.synthesisBytes)], ['Clips', formatBytes(stats.voiceBytes)],
    ]
    dom.historyStats.replaceChildren(...values.map(([label, value]) => {
      const card = element('div', 'history-stat')
      card.append(element('strong', '', String(value)), element('span', '', label))
      return card
    }))
  }

  function makeSynthesisCard(item) {
    const card = element('article', 'history-card')
    const head = element('div', 'history-card-head')
    const title = element('div')
    title.append(element('h3', '', `${item.provider} · ${item.voice}`), element('time', '', formatDate(item.createdAt)))
    head.append(title, element('span', 'history-badge', item.format.toUpperCase()))
    const audio = document.createElement('audio')
    audio.controls = true
    audio.preload = 'metadata'
    audio.src = item.audioUrl
    const actions = element('div', 'history-actions')
    actions.append(
      historyAction('Reutiliser', 'ghost', () => reuseSynthesis(item)),
      historyAction('Telecharger', 'ghost', () => downloadUrl(`${item.audioUrl}?download=1`)),
      historyAction('Tous formats (.ZIP)', 'ghost', () => downloadUrl(`${item.audioUrl}/formats?formats=wav,mp3,ogg,flac,m4a`)),
      historyAction('Supprimer', 'danger', () => deleteHistoryItem('syntheses', item.id)),
    )
    card.append(head, element('p', 'history-text', item.text), audio, actions)
    return card
  }

  function makeVoiceCard(item) {
    const card = element('article', 'history-card')
    const head = element('div', 'history-card-head')
    const title = element('div')
    title.append(element('h3', '', item.name), element('time', '', formatDate(item.createdAt)))
    head.append(title, element('span', 'history-badge', item.source === 'recorded' ? 'Enregistree' : 'Importee'))
    const audio = document.createElement('audio')
    audio.controls = true
    audio.preload = 'metadata'
    audio.src = item.audioUrl
    const actions = element('div', 'history-actions')
    actions.append(
      historyAction('Utiliser avec Michel', 'ghost', () => useStoredVoice(item)),
      historyAction('Renommer', 'ghost', () => renameStoredVoice(item)),
      historyAction('Telecharger', 'ghost', () => downloadUrl(`${item.audioUrl}?download=1`)),
      historyAction('Supprimer', 'danger', () => deleteHistoryItem('voices', item.id)),
    )
    card.append(head, audio, actions)
    return card
  }

  function renderHistory() {
    const { stats, syntheses, voices } = state.history
    renderHistoryStats(stats || { syntheses: 0, voices: 0, synthesisBytes: 0, voiceBytes: 0 })
    dom.synthesesList.replaceChildren(
      ...(syntheses.length ? syntheses.map(makeSynthesisCard) : [element('p', 'history-empty', 'Aucune synthese enregistree.')]),
    )
    dom.voicesList.replaceChildren(
      ...(voices.length ? voices.map(makeVoiceCard) : [element('p', 'history-empty', 'Aucune voix importee ou enregistree.')]),
    )
  }

  async function loadHistory(force = false) {
    if (state.historyLoading || (!force && state.history.stats)) return
    state.historyLoading = true
    dom.historyRefresh.disabled = true
    try {
      state.history = await apiGet('/api/history?limit=200')
      renderHistory()
    } catch (error) {
      const message = element('p', 'status err history-status', `Historique indisponible : ${error.message}`)
      dom.historyStats.replaceChildren(message)
    } finally {
      state.historyLoading = false
      dom.historyRefresh.disabled = false
    }
  }

  function downloadUrl(url) {
    const link = document.createElement('a')
    link.href = url
    link.download = ''
    document.body.append(link)
    link.click()
    link.remove()
  }

  async function deleteHistoryItem(kind, id) {
    if (!window.confirm('Supprimer definitivement cet element ?')) return
    try {
      await apiSend(`/api/history/${kind}/${id}`, 'DELETE')
      if (kind === 'voices' && state.refVoiceId === id) {
        state.refVoiceId = null
        dom.clipReady.hidden = true
      }
      state.history.stats = null
      await loadHistory(true)
    } catch (error) {
      window.alert(`Suppression impossible : ${error.message}`)
    }
  }

  async function clearHistory(kind) {
    const label = kind === 'syntheses' ? 'toutes les synthèses' : 'toutes les voix'
    if (!window.confirm(`Supprimer ${label} ?`)) return
    try {
      await apiSend(`/api/history/${kind}`, 'DELETE')
      if (kind === 'voices') {
        state.refVoiceId = null
        dom.clipReady.hidden = true
      }
      state.history.stats = null
      await loadHistory(true)
    } catch (error) {
      window.alert(`Suppression impossible : ${error.message}`)
    }
  }

  async function renameStoredVoice(item) {
    const name = window.prompt('Nouveau nom de la voix', item.name)
    if (name === null || !name.trim() || name.trim() === item.name) return
    try {
      await apiSend(`/api/history/voices/${item.id}`, 'PATCH', { name: name.trim() })
      state.history.stats = null
      await loadHistory(true)
    } catch (error) {
      window.alert(`Renommage impossible : ${error.message}`)
    }
  }

  function useStoredVoice(item) {
    state.refVoiceId = item.id
    state.provider = 'mimo'
    dom.clipReady.hidden = false
    dom.clipReady.textContent = `Clip pret : ${item.name}`
    window.location.hash = '#/'
    renderProvider()
  }

  function reuseSynthesis(item) {
    state.provider = item.provider
    state.text = item.text
    state.speed = item.speed
    state.gain = item.gain
    state.formats = [`.${String(item.format || 'wav').replace(/^\./, '')}`]
    if (item.provider === 'edge') state.edgeVoice = item.voice
    if (item.provider === 'gemini') state.geminiVoice = item.voice
    if (item.provider === 'mimo') {
      state.refVoiceId = item.referenceVoiceId || null
      dom.clipReady.hidden = !state.refVoiceId
      if (state.refVoiceId) dom.clipReady.textContent = 'Clip historique selectionne'
    }
    dom.text.value = item.text
    dom.speed.value = item.speed
    dom.gain.value = item.gain
    dom.speedLabel.textContent = `Vitesse ${item.speed.toFixed(2)}x`
    dom.gainLabel.textContent = `Volume ${Math.round(item.gain * 100)}%`
    if (state.config) {
      dom.edgeVoice.value = state.edgeVoice
      dom.geminiVoice.value = state.geminiVoice
      renderFormats()
      renderProvider()
    }
    updateTextState()
    window.location.hash = '#/'
  }

  function renderRoute() {
    const route = window.location.hash
    const vitrine = route === '#/vitrine'
    const historyPage = route === '#/history'
    dom.vitrine.hidden = !vitrine
    dom.historyPage.hidden = !historyPage
    dom.loading.hidden = vitrine || historyPage || Boolean(state.config)
    dom.studio.hidden = vitrine || historyPage || !state.config
    if (historyPage) loadHistory()
  }

  function updateTextState() {
    const limits = currentLimits()
    const metrics = textMetrics(dom.text.value)
    const warn = metrics.chars > limits.maxChars * 0.8
      || metrics.words > limits.maxWords * 0.8
      || metrics.sentences > limits.maxSentences * 0.8
    const over = metrics.chars > limits.maxChars
      || metrics.words > limits.maxWords
      || metrics.sentences > limits.maxSentences
    updateTextState.over = over
    dom.textChars.textContent = formatNumber(metrics.chars)
    dom.textWords.textContent = formatNumber(metrics.words)
    dom.textSentences.textContent = formatNumber(metrics.sentences)
    dom.textCounter.textContent = `${formatNumber(metrics.chars)} / ${formatNumber(limits.maxChars)} caracteres`
    dom.textCounter.className = `counter ${over ? 'over' : warn ? 'warn' : 'ok'}`
    dom.limitMessage.hidden = !over
    dom.limitMessage.textContent = over
      ? `Plafond ${limits.name} depasse : ${formatNumber(metrics.chars)} caracteres, ${formatNumber(metrics.words)} mots, ${formatNumber(metrics.sentences)} phrases. Reduisez ou decoupez le texte.`
      : ''
    dom.synthesize.disabled = state.busy || !selectedFormats().length || over
  }

  function renderWave() {
    const bars = []
    for (let index = 0; index < 28; index += 1) {
      const bar = element('span')
      bar.style.animationDelay = `${(index % 7) * 90}ms`
      bars.push(bar)
    }
    dom.wave.replaceChildren(...bars)
  }

  async function sendRefBlob(blob, filename, source = 'uploaded', name = '') {
    const body = new FormData()
    body.append('file', blob, filename)
    body.append('source', source)
    body.append('name', name)
    setStatus('wait', "Envoi de l'audio de reference...")
    try {
      const response = await fetch('/api/upload-ref', { method: 'POST', body })
      const data = await responseData(response)
      if (!response.ok) throw new Error(errorText(data, response.status))
      state.refVoiceId = data.voice.id
      state.history.stats = null
      dom.clipReady.hidden = false
      dom.clipReady.textContent = `Clip pret : ${data.voice.name}`
      setStatus('ok', `Audio de reference : ${data.voice.name}`)
    } catch (error) {
      setStatus('err', `Envoi impossible : ${error.message}`)
    }
  }

  function uploadRef(file) {
    if (file) return sendRefBlob(file, file.name || 'reference.wav', 'uploaded', file.name || 'Voix importee')
    return Promise.resolve()
  }

  function stopRecording() {
    const recorder = state.recorder
    if (recorder && recorder.state !== 'inactive') recorder.stop()
  }

  function updateRecordButton() {
    const minutes = String(Math.floor(state.recordSeconds / 60)).padStart(2, '0')
    const seconds = String(state.recordSeconds % 60).padStart(2, '0')
    dom.recordButton.textContent = state.recorder?.state === 'recording'
      ? `Stop (${minutes}:${seconds})` : 'Enregistrer (micro)'
  }

  async function startRecording() {
    if (state.recorder?.state === 'recording') return
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      setStatus('err', 'Micro non supporte par ce navigateur (utilisez http://127.0.0.1 pour autoriser le micro).')
      return
    }
    let stream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (error) {
      setStatus('err', `Acces micro refuse : ${error.message}`)
      return
    }

    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']
      .find((type) => window.MediaRecorder.isTypeSupported?.(type)) || ''
    const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)
    state.recorder = recorder
    state.recordStream = stream
    state.recordChunks = []
    state.recordSeconds = 0

    recorder.ondataavailable = (event) => {
      if (event.data?.size) state.recordChunks.push(event.data)
    }
    recorder.onstop = async () => {
      clearInterval(state.recordTimer)
      clearTimeout(state.recordAutoStop)
      state.recordTimer = null
      state.recordAutoStop = null
      state.recordStream?.getTracks().forEach((track) => track.stop())
      state.recordStream = null
      dom.recordButton.classList.remove('recording')
      updateRecordButton()
      const type = recorder.mimeType || mime || 'audio/webm'
      const blob = new Blob(state.recordChunks, { type: type.split(';')[0] })
      state.recordChunks = []
      if (!blob.size) {
        setStatus('err', 'Enregistrement vide : parlez plus longtemps.')
        return
      }
      setStatus('wait', "Conversion de l'enregistrement en WAV...")
      try {
        const wav = await blobToWav(blob)
        const recordedName = `Micro ${new Date().toLocaleString('fr-FR')}`
        await sendRefBlob(wav, `micro_${Date.now()}.wav`, 'recorded', recordedName)
      } catch (error) {
        setStatus('err', `Conversion audio impossible : ${error.message}`)
      }
    }

    recorder.start()
    dom.recordButton.classList.add('recording')
    updateRecordButton()
    state.recordTimer = setInterval(() => {
      state.recordSeconds += 1
      updateRecordButton()
    }, 1000)
    state.recordAutoStop = setTimeout(stopRecording, 30000)
    setStatus('wait', 'Enregistrement... parlez puis cliquez Stop.')
  }

  function setBusy(busy) {
    state.busy = busy
    updateTextState()
    dom.synthesize.textContent = busy ? 'Synthese...' : 'Synthetiser'
    dom.busy.hidden = !busy
    if (busy) dom.result.hidden = true
  }

  async function synthesize() {
    const text = dom.text.value
    if (!text.trim() || state.busy) return
    const limits = currentLimits()
    const metrics = textMetrics(text)
    if (metrics.chars > limits.maxChars || metrics.words > limits.maxWords || metrics.sentences > limits.maxSentences) {
      setStatus('err', `Texte au-dessus du plafond ${limits.name}. Reduisez-le ou decoupez-le.`)
      return
    }
    if (state.provider === 'mimo' && !state.refVoiceId) {
      setStatus('err', 'Ajoutez un clip audio de reference avant de synthetiser avec Michel.')
      return
    }

    setBusy(true)
    setStatus('wait', 'Synthese en cours...')
    const payload = {
      text,
      provider: state.provider,
      voice: state.provider === 'gemini' ? state.geminiVoice
        : state.provider === 'edge' ? state.edgeVoice : '',
      speed: state.speed,
      gain: state.gain,
      formats: selectedFormats(),
      refVoiceId: state.refVoiceId,
      refText: '',
      mimoUrl: state.mimoUrl,
      mimoModel: state.mimoModel,
    }

    try {
      const response = await fetch('/api/synthesize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const data = await response.json().catch(() => null)
        throw new Error(errorText(data, response.status))
      }
      const blob = await response.blob()
      if (state.audioUrl) URL.revokeObjectURL(state.audioUrl)
      state.audioUrl = URL.createObjectURL(blob)
      const formats = (response.headers.get('X-Formats') || selectedFormats().join(',')).split(',').filter(Boolean)
      state.formats = formats
      const isArchive = formats.length > 1
      const historyId = response.headers.get('X-History-Id')
      const extension = isArchive ? 'zip' : (response.headers.get('X-Primary-Format') || formats[0] || '.wav').replace(/^\./, '')
      dom.audio.src = isArchive && historyId
        ? `/api/history/syntheses/${historyId}/audio`
        : state.audioUrl
      dom.download.href = state.audioUrl
      dom.download.download = isArchive ? 'synthese-formats.zip' : `synthese.${extension}`
      dom.download.textContent = isArchive
        ? `Telecharger ${formats.length} formats (.ZIP)`
        : `Telecharger (${extension.toUpperCase()})`
      dom.result.hidden = false
      state.history.stats = null
      setStatus('ok', `Termine en ${response.headers.get('X-Elapsed') || '?'} s.`)
      setTimeout(() => dom.audio.play().catch(() => {}), 50)
    } catch (error) {
      setStatus('err', error.message)
    } finally {
      setBusy(false)
    }
  }

  async function loadHealth() {
    try {
      const health = await apiGet('/api/health')
      dom.healthDot.className = `dot ${health.geminiKey ? 'on' : 'off'}`
      dom.healthDot.title = health.geminiKey ? 'Eben est configure' : "Eben n'est pas configure"
      dom.healthText.textContent = health.geminiKey
        ? 'Eben est configure' : "Eben n'est pas configure ; Dane reste disponible"
    } catch {
      dom.healthDot.className = 'dot off'
      dom.healthText.textContent = 'Etat du service indisponible'
    }
  }

  async function loadConfig() {
    const config = await apiGet('/api/config')
    state.config = config
    state.geminiVoice = config.defaultVoice
    state.edgeVoice = config.defaultEdgeVoice
    state.mimoUrl = config.mimo.defaultMimoUrl
    state.mimoModel = config.mimo.defaultMimoModel
    renderProviders()
    renderGeminiVoices()
    renderFormats()
    renderProvider()
    renderRoute()
  }

  async function loadEdgeVoices() {
    try {
      const data = await apiGet('/api/voices/edge')
      state.edgeVoices = data.voices || []
    } catch {
      state.edgeVoices = []
    } finally {
      state.voicesLoading = false
      renderProvider()
    }
  }

  function bindEvents() {
    window.addEventListener('hashchange', renderRoute)
    dom.geminiVoice.addEventListener('change', () => { state.geminiVoice = dom.geminiVoice.value })
    dom.edgeVoice.addEventListener('change', () => { state.edgeVoice = dom.edgeVoice.value })
    dom.edgeSearch.addEventListener('input', () => {
      state.edgeSearch = dom.edgeSearch.value
      renderEdgeVoices()
    })
    dom.refFile.addEventListener('change', () => uploadRef(dom.refFile.files?.[0]))
    dom.recordButton.addEventListener('click', () => {
      if (state.recorder?.state === 'recording') stopRecording()
      else startRecording()
    })
    dom.speed.addEventListener('input', () => {
      state.speed = Number(dom.speed.value)
      dom.speedLabel.textContent = `Vitesse ${state.speed.toFixed(2)}x`
    })
    dom.gain.addEventListener('input', () => {
      state.gain = Number(dom.gain.value)
      dom.gainLabel.textContent = `Volume ${Math.round(state.gain * 100)}%`
    })
    dom.text.addEventListener('input', updateTextState)
    dom.historyRefresh.addEventListener('click', () => loadHistory(true))
    dom.clearSyntheses.addEventListener('click', () => clearHistory('syntheses'))
    dom.clearVoices.addEventListener('click', () => clearHistory('voices'))
    dom.synthesize.addEventListener('click', synthesize)
    window.addEventListener('beforeunload', () => {
      if (state.audioUrl) URL.revokeObjectURL(state.audioUrl)
      clearInterval(state.recordTimer)
      clearTimeout(state.recordAutoStop)
      state.recordStream?.getTracks().forEach((track) => track.stop())
    })
  }

  function init() {
    renderVitrine()
    renderRoute()
    renderWave()
    bindEvents()
    updateTextState()
    loadHealth()
    loadConfig().catch((error) => {
      dom.loading.replaceChildren(element('p', 'status err', `Le studio ne peut pas charger : ${error.message}`))
    })
    loadEdgeVoices()
  }

  init()
})()
