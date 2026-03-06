import { getApiUrl } from '../config/apiConfig';

export const sendIntervention = async () => {
  try {
    const API_BASE_URL = getApiUrl();
    const response = await fetch(
      `${API_BASE_URL}/api/orchestrator/intervention`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      }
    );

    if (response.ok) {
      const result = await response.json();
      alert(`Intervention signal sent successfully: ${result.message}`);
    } else {
      const errorText = await response.text();
      alert(
        `Error: Could not send intervention signal. Server responded with: ${response.status} ${response.statusText}. Details: ${errorText}`
      );
    }
  } catch (error) {
    alert(`Failed to send intervention signal: ${error.message}`);
  }
};
