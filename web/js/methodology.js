import { getFactors } from './api.js'
import { escapeHtml } from './view.js'
import { applyDocumentLanguage, applyToDocument, t } from './i18n.js'

applyDocumentLanguage()
applyToDocument()

const content = document.getElementById('factor-content')

try {
  const factors = await getFactors()
  content.innerHTML = `${factors.factor_set?.is_mock ? `<aside class="disclaimer"><span class="info-icon" aria-hidden="true">i</span><div><strong>${escapeHtml(t('Placeholder data'))}</strong><p>${escapeHtml(t('This published factor set is marked as mock and is not a verified scientific result.'))}</p></div></aside>` : ''}
    <article class="review-block"><h2>${escapeHtml(t('Factor set'))}</h2><dl class="review-destinations"><div><dt>${escapeHtml(t('Version'))}</dt><dd>${escapeHtml(factors.factor_set?.version_label || t('Not supplied'))}</dd></div><div><dt>${escapeHtml(t('Published'))}</dt><dd>${escapeHtml(factors.factor_set?.published_at || t('Not supplied'))}</dd></div><div><dt>${escapeHtml(t('Notes'))}</dt><dd>${escapeHtml(factors.factor_set?.notes || t('Not supplied'))}</dd></div></dl></article>
    <article class="review-block"><h2>${escapeHtml(t('Published formulas'))}</h2>${(factors.formulas || []).length ? `<div class="table-scroll" tabindex="0"><table><thead><tr><th scope="col">${escapeHtml(t('Metric'))}</th><th scope="col">${escapeHtml(t('Expression'))}</th><th scope="col">${escapeHtml(t('Notes'))}</th></tr></thead><tbody>${factors.formulas.map(formula => `<tr><th scope="row">${escapeHtml(formula.metric)}</th><td><code>${escapeHtml(formula.expression)}</code></td><td>${escapeHtml(formula.notes || '')}</td></tr>`).join('')}</tbody></table></div>` : `<p class="empty-state">${escapeHtml(t('No published formulas were returned.'))}</p>`}</article>`
} catch (error) {
  content.innerHTML = `<div class="error-state"><h2>${escapeHtml(t('Methodology unavailable'))}</h2><p>${escapeHtml(error.message)}</p></div>`
}
