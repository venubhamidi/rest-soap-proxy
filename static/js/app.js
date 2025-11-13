// SOAP-to-REST Proxy - Frontend JavaScript

// Load services on page load
document.addEventListener('DOMContentLoaded', () => {
    loadServices();
});

// Handle WSDL upload form
document.getElementById('upload-form').addEventListener('submit', async (e) => {
    e.preventDefault();

    const submitBtn = document.getElementById('submit-btn');
    const btnText = submitBtn.querySelector('.btn-text');
    const btnLoader = submitBtn.querySelector('.btn-loader');
    const resultBox = document.getElementById('upload-result');

    // Disable submit button
    submitBtn.disabled = true;
    btnText.style.display = 'none';
    btnLoader.style.display = 'inline';

    // Get form data
    const formData = new FormData();
    const wsdlFile = document.getElementById('wsdl-file').files[0];
    const wsdlUrl = document.getElementById('wsdl-url').value;
    const serviceName = document.getElementById('service-name').value;
    const autoRegister = document.getElementById('auto-register')?.checked || false;

    if (wsdlFile) {
        formData.append('wsdl_file', wsdlFile);
    } else if (wsdlUrl) {
        formData.append('wsdl_url', wsdlUrl);
    } else {
        showResult(resultBox, 'error', 'Please provide either a WSDL file or URL');
        resetSubmitButton(submitBtn, btnText, btnLoader);
        return;
    }

    if (serviceName) {
        formData.append('service_name', serviceName);
    }

    formData.append('auto_register_gateway', autoRegister);

    try {
        const response = await fetch('/api/convert', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (response.ok) {
            let message = `<h3>✅ Success!</h3>`;
            message += `<p><strong>Service:</strong> ${data.service_name}</p>`;
            message += `<p><strong>Operations:</strong> ${data.operations_count}</p>`;

            if (data.gateway_registered) {
                message += `<p><strong>Gateway:</strong> ✅ Registered</p>`;
                message += `<p><strong>MCP Endpoint:</strong></p>`;
                message += `<code>${data.mcp_endpoint}</code>`;
            } else if (data.gateway_error) {
                message += `<p><strong>Gateway:</strong> ⚠️ Registration failed - ${data.gateway_error}</p>`;
            }

            showResult(resultBox, 'success', message);

            // Reset form
            document.getElementById('upload-form').reset();

            // Reload services list
            setTimeout(() => loadServices(), 500);

        } else {
            showResult(resultBox, 'error', `<h3>❌ Error</h3><p>${data.error}</p>`);
        }

    } catch (error) {
        showResult(resultBox, 'error', `<h3>❌ Error</h3><p>${error.message}</p>`);
    } finally {
        resetSubmitButton(submitBtn, btnText, btnLoader);
    }
});

// Show result message
function showResult(element, type, message) {
    element.className = `result-box ${type}`;
    element.innerHTML = message;
    element.style.display = 'block';

    // Auto-hide after 10 seconds
    setTimeout(() => {
        element.style.display = 'none';
    }, 10000);
}

// Reset submit button
function resetSubmitButton(btn, textEl, loaderEl) {
    btn.disabled = false;
    textEl.style.display = 'inline';
    loaderEl.style.display = 'none';
}

// Load services list
async function loadServices() {
    const servicesList = document.getElementById('services-list');
    servicesList.innerHTML = '<p class="loading">Loading services...</p>';

    try {
        const response = await fetch('/api/services');
        const data = await response.json();

        if (data.services.length === 0) {
            servicesList.innerHTML = '<p class="loading">No services registered yet. Upload a WSDL to get started!</p>';
            return;
        }

        servicesList.innerHTML = '';

        data.services.forEach(service => {
            servicesList.appendChild(createServiceCard(service));
        });

    } catch (error) {
        servicesList.innerHTML = `<p class="loading" style="color: red;">Error loading services: ${error.message}</p>`;
    }
}

// Create service card element
function createServiceCard(service) {
    const card = document.createElement('div');
    card.className = 'service-card';
    card.id = `service-${service.id}`;

    let html = `
        <h3>🧼 ${service.name}</h3>
        <div class="service-meta">
            <p>${service.description || 'No description'}</p>
            <p>Operations: ${service.operations_count} | Created: ${new Date(service.created_at).toLocaleDateString()}</p>
        </div>
    `;

    // Gateway status
    html += '<div class="gateway-status">';
    if (service.gateway_registered) {
        html += `
            <span class="badge success">✅ Registered with Gateway</span>
            <div class="endpoint-code">${service.gateway_mcp_endpoint}</div>
            <button class="btn btn-danger btn-small" onclick="unregisterGateway('${service.id}')">
                Unregister from Gateway
            </button>
        `;
    } else {
        html += `
            <span class="badge warning">⚠️ Not registered with Gateway</span>
            <button class="btn btn-success btn-small" onclick="registerGateway('${service.id}')">
                Register with Gateway
            </button>
        `;
    }
    html += '</div>';

    // Actions
    html += `
        <div class="actions">
            <button class="btn btn-secondary" onclick="downloadOpenAPI('${service.id}', 'yaml')">
                📥 Download OpenAPI (YAML)
            </button>
            <button class="btn btn-secondary" onclick="downloadOpenAPI('${service.id}', 'json')">
                📥 Download OpenAPI (JSON)
            </button>
            <button class="btn btn-danger" onclick="deleteService('${service.id}', '${service.name}')">
                🗑️ Delete Service
            </button>
        </div>
    `;

    card.innerHTML = html;
    return card;
}

// Download OpenAPI spec
function downloadOpenAPI(serviceId, format) {
    window.location.href = `/api/services/${serviceId}/openapi.${format}`;
}

// Register service with Gateway
async function registerGateway(serviceId) {
    if (!confirm('Register this service with MCP Gateway?')) {
        return;
    }

    try {
        const response = await fetch(`/api/services/${serviceId}/register-gateway`, {
            method: 'POST'
        });

        const data = await response.json();

        if (response.ok) {
            alert(`✅ Registered with Gateway!\n\nMCP Endpoint:\n${data.mcp_endpoint}`);
            loadServices();
        } else {
            alert(`❌ Error: ${data.error}`);
        }

    } catch (error) {
        alert(`❌ Error: ${error.message}`);
    }
}

// Unregister service from Gateway
async function unregisterGateway(serviceId) {
    if (!confirm('Unregister this service from MCP Gateway?')) {
        return;
    }

    try {
        const response = await fetch(`/api/services/${serviceId}/unregister-gateway`, {
            method: 'DELETE'
        });

        const data = await response.json();

        if (response.ok) {
            alert('✅ Unregistered from Gateway successfully!');
            loadServices();
        } else {
            alert(`❌ Error: ${data.error}`);
        }

    } catch (error) {
        alert(`❌ Error: ${error.message}`);
    }
}

// Delete service
async function deleteService(serviceId, serviceName) {
    if (!confirm(`Delete service "${serviceName}"?\n\nThis will also unregister it from Gateway if registered.`)) {
        return;
    }

    try {
        const response = await fetch(`/api/services/${serviceId}`, {
            method: 'DELETE'
        });

        const data = await response.json();

        if (response.ok) {
            alert('✅ Service deleted successfully!');
            loadServices();
        } else {
            alert(`❌ Error: ${data.error}`);
        }

    } catch (error) {
        alert(`❌ Error: ${error.message}`);
    }
}
