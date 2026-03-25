// Extraction page controller
// Handles task submission, progress polling, and task list actions.

// Track selected fields for export links after completion.
let currentSelectedFields = [];
let currentTaskMaxResults = 50;

$(document).ready(function() {
    loadRecentTasks();

    $('#extractionForm').on('submit', function(e) {
        e.preventDefault();
        startExtraction();
    });

    $('#selectAllFields').on('click', function() {
        $('input[name="selected_fields"]').prop('checked', true);
    });

    $('#deselectAllFields').on('click', function() {
        $('input[name="selected_fields"]').prop('checked', false);
    });

    $('#source').on('change', function() {
        const isListCrawl = $(this).val() === 'list_crawl';
        
        if (isListCrawl) {
            // For list-based crawl: keyword is the list type, location is optional
            $('#keyword-label-text').text('Target List Type');
            $('#keyword').attr('placeholder', 'e.g., NC aging, FL aging, Nursing Homes');
            $('#keyword').attr('required', 'required');
            $('#location').removeAttr('required');
            $('#keyword-helper-text').show();
            $('#location-helper-text').show();
        } else {
            // For other sources: both keyword and location required
            $('#keyword-label-text').text('Keyword / Industry');
            $('#keyword').attr('placeholder', 'e.g., restaurant, dentist, plumber');
            $('#keyword').attr('required', 'required');
            $('#location').attr('required', 'required');
            $('#keyword-helper-text').hide();
            $('#location-helper-text').hide();
        }
    });
});

function getSelectedFields() {
    const fields = [];
    $('input[name="selected_fields"]:checked').each(function() {
        fields.push($(this).val());
    });
    return fields;
}

function startExtraction() {
    const selectedFields = getSelectedFields();
    if (selectedFields.length === 0) {
        showToast('Please select at least one field to extract.', 'warning');
        return;
    }

    const source = $('#source').val();
    const keyword = $('#keyword').val() || '';
    const location = $('#location').val() || '';
    
    // For list_crawl, only keyword is required (location defaults to US)
    if (source === 'list_crawl' && !keyword) {
        showToast('Please specify the target list type.', 'warning');
        return;
    }
    
    // For other sources, both keyword and location are required
    if (source !== 'list_crawl' && (!keyword || !location)) {
        showToast('Please fill in keyword and location.', 'warning');
        return;
    }

    currentSelectedFields = selectedFields;

    const formData = {
        keyword: keyword,
        location: location,
        source,
        radius: $('#radius').val(),
        max_results: parseInt($('#max_results').val(), 10) || 50,
        selected_fields: selectedFields,
        enable_validation: $('#enable_validation').is(':checked'),
    };

    $('#startExtraction').prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i> Starting...');

    $.ajax({
        url: '/api/extraction/start',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(formData),
        success: function(response) {
            $('#startExtraction').prop('disabled', false).html('<i class="fas fa-play"></i> Start Extraction');

            if (response.status === 'success') {
                if (Array.isArray(response.selected_fields) && response.selected_fields.length > 0) {
                    currentSelectedFields = response.selected_fields;
                }
                currentTaskMaxResults = parseInt(response.max_results, 10) || formData.max_results || 50;
                showProgress(response.task_id);
                checkTaskStatus(response.task_id);
                showToast('Extraction started successfully!', 'success');
            } else {
                showToast('Error: ' + response.message, 'danger');
            }
        },
        error: function(xhr) {
            $('#startExtraction').prop('disabled', false).html('<i class="fas fa-play"></i> Start Extraction');
            const apiError = xhr.responseJSON?.error || xhr.statusText || 'unknown';
            if (xhr.status === 403 && xhr.responseJSON?.code === 'demo_locked') {
                showToast(apiError, 'warning');
                return;
            }
            showToast('Error starting extraction: ' + apiError, 'danger');
        }
    });
}

function showProgress(taskId) {
    $('#progressSection').slideDown(400);
    $('#taskId').text(taskId);
    $('#taskStatus').html('<span class="pulse-dot"></span> <span style="color: #93c5fd; font-weight: 600;">Processing...</span>');
    $('#recordsFound').text('0');
    $('#progressBar').css('width', '0%').removeClass('completed failed');
    $('#exportContainer').html('');
    $('#stopContainer').show();
}

function checkTaskStatus(taskId) {
    const interval = setInterval(function() {
        $.get('/api/extraction/status/' + taskId, function(data) {
            const status = data.status;
            const records = data.total_records || 0;
            $('#recordsFound').text(records);

            if (status === 'running' || status === 'enriching') {
                $('#taskStatus').html('<span class="pulse-dot"></span> <span style="color: #93c5fd; font-weight: 600;">Running — extracting data...</span>');
                const target = Math.max(currentTaskMaxResults || 50, 1);
                const pct = Math.min(98, Math.round((records / target) * 100));
                $('#progressBar').css('width', pct + '%');
            } else if (status === 'completed') {
                $('#stopContainer').hide();
                $('#progressBar').css('width', '100%').addClass('completed');
                $('#taskStatus').html('<i class="fas fa-check-circle" style="color: var(--success);"></i> <span style="color: #86efac; font-weight: 600;">Completed</span>');
                clearInterval(interval);
                loadRecentTasks();
                showExportOptions(taskId);
                showToast('Extraction completed! ' + records + ' records found.', 'success');
            } else if (status === 'stopped') {
                $('#stopContainer').hide();
                $('#progressBar').addClass('failed');
                $('#taskStatus').html('<i class="fas fa-hand-paper" style="color: var(--warning);"></i> <span style="color: #fcd34d; font-weight: 600;">Stopped</span>');
                clearInterval(interval);
                loadRecentTasks();
                if (records > 0) showExportOptions(taskId);
                showToast('Extraction stopped. ' + records + ' records saved.', 'warning');
            } else if (status === 'failed') {
                $('#stopContainer').hide();
                $('#progressBar').css('width', '100%').addClass('failed');
                $('#taskStatus').html('<i class="fas fa-times-circle" style="color: var(--danger);"></i> <span style="color: #fca5a5; font-weight: 600;">Failed</span>');
                clearInterval(interval);
                loadRecentTasks();
                showToast('Extraction failed. Please try again.', 'danger');
            } else {
                $('#taskStatus').html('<span style="color: var(--text-secondary);">' + status + '</span>');
            }
        }).fail(function(xhr) {
            if (xhr.status === 403 && xhr.responseJSON?.code === 'demo_locked') {
                clearInterval(interval);
                $('#stopContainer').hide();
                $('#taskStatus').html('<i class="fas fa-lock" style="color: var(--warning);"></i> <span style="color: #fcd34d; font-weight: 600;">Extraction limit reached</span>');
                showToast(xhr.responseJSON?.error || 'Extraction limit reached.', 'warning');
                return;
            }
            clearInterval(interval);
            showToast('Unable to fetch task status.', 'danger');
        });
    }, 2000);
}

function buildFieldsQueryParam() {
    const fields = currentSelectedFields.length > 0 ? currentSelectedFields : [];
    if (fields.length > 0) {
        return '?fields=' + encodeURIComponent(fields.join(','));
    }
    return '';
}

function showExportOptions(taskId) {
    const fieldsParam = buildFieldsQueryParam();
    const exportHtml = `
        <div class="export-options" style="margin-top: 20px;">
            <a href="/api/export/csv/${taskId}${fieldsParam}" class="btn-success-dark">
                <i class="fas fa-file-csv"></i> Export CSV
            </a>
            <a href="/api/export/excel/${taskId}${fieldsParam}" class="btn-success-dark">
                <i class="fas fa-file-excel"></i> Export Excel
            </a>
            <button class="btn-info-dark" onclick="viewResults(${taskId})">
                <i class="fas fa-eye"></i> View Results
            </button>
        </div>
    `;
    $('#exportContainer').html(exportHtml);
}

function loadRecentTasks() {
    $.get('/api/extraction/tasks/recent', function(data) {
        let html = '';
        if (!data.tasks || data.tasks.length === 0) {
            html = `<tr><td colspan="8">
                <div class="empty-state">
                    <div class="empty-icon"><i class="fas fa-inbox"></i></div>
                    <h4>No tasks yet</h4>
                    <p>Start your first extraction above to see it here.</p>
                </div>
            </td></tr>`;
        } else {
            data.tasks.forEach(function(task) {
                const statusClass = task.status === 'completed' ? 'completed' :
                                   (task.status === 'running' || task.status === 'enriching') ? 'running' :
                                   task.status === 'stopped' ? 'pending' :
                                   task.status === 'failed' ? 'failed' : 'pending';
                const statusIcon = task.status === 'completed' ? 'check-circle' :
                                  (task.status === 'running' || task.status === 'enriching') ? 'spinner fa-spin' :
                                  task.status === 'stopped' ? 'hand-paper' :
                                  task.status === 'failed' ? 'times-circle' : 'clock';
                const statusLabel = task.status === 'enriching' ? 'running' : task.status;

                html += `
                    <tr>
                        <td style="font-weight: 600; color: var(--text-primary);">#${task.id}</td>
                        <td style="color: var(--text-primary); font-weight: 500;">${task.keyword}</td>
                        <td>${task.location}</td>
                        <td><span class="source-badge"><i class="fas fa-globe"></i> ${task.source}</span></td>
                        <td><span class="status-badge ${statusClass}"><i class="fas fa-${statusIcon}"></i> ${statusLabel}</span></td>
                        <td style="font-weight: 600; color: var(--accent-violet);">${task.total_records}</td>
                        <td style="font-size: 13px;">${new Date(task.created_at).toLocaleString()}</td>
                        <td>
                            <div style="display: flex; gap: 6px;">
                                <a href="/results/${task.id}" class="btn-info-dark btn-sm" title="View">
                                    <i class="fas fa-eye"></i>
                                </a>
                                ${task.total_records > 0 ? `
                                    <a href="/api/export/csv/${task.id}" class="btn-success-dark btn-sm" title="Download CSV">
                                        <i class="fas fa-download"></i>
                                    </a>
                                ` : ''}
                                ${(task.status === 'running' || task.status === 'enriching') ? `
                                    <button class="btn-warning-dark btn-sm" onclick="stopExtraction(${task.id})" title="Stop">
                                        <i class="fas fa-stop-circle"></i>
                                    </button>
                                ` : ''}
                                <button class="btn-danger-dark btn-sm" onclick="deleteTask(${task.id})" title="Delete">
                                    <i class="fas fa-trash"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                `;
            });
        }

        $('#tasksTableBody').html(html);
    });
}

function viewResults(taskId) {
    window.location.href = '/results/' + taskId;
}

function stopExtraction(taskId) {
    if (!confirm('Are you sure you want to stop this extraction?')) return;
    $.ajax({
        url: '/api/extraction/stop/' + taskId,
        method: 'POST',
        success: function() {
            showToast('Stop signal sent.', 'info');
        },
        error: function(xhr) {
            showToast('Failed to stop task: ' + (xhr.responseJSON?.message || xhr.statusText), 'danger');
        }
    });
}

function deleteTask(taskId) {
    if (!confirm('Are you sure you want to delete task #' + taskId + ' and all its records?')) return;
    $.ajax({
        url: '/api/extraction/tasks/' + taskId,
        method: 'DELETE',
        success: function() {
            loadRecentTasks();
            showToast('Task #' + taskId + ' deleted.', 'info');
        },
        error: function(xhr) {
            showToast('Failed to delete task: ' + (xhr.responseJSON?.message || xhr.statusText), 'danger');
        }
    });
}
