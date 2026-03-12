/**
 * useServiceDiscovery
 *
 * Owns all state and data-fetching for the Service Discovery accordion section
 * in UnifiedServicesPanel. Extracted from UnifiedServicesPanel.jsx (#304).
 *
 * State managed:
 *   services, loadingServices, error, selectedCapabilities, selectedPhases,
 *   searchQuery, healthStatus
 *
 * Also exposes derived values: allCapabilities, allPhases, filteredServices.
 */
import { useState, useCallback, useMemo } from 'react';
import logger from '@/lib/logger';
import phase4Client from '../services/phase4Client';

/**
 * @param {object} params
 * @param {Function} [params.onError] - optional; called with an error message
 *   string when the service fetch fails.
 */
const useServiceDiscovery = ({ onError } = {}) => {
  const [services, setServices] = useState([]);
  const [loadingServices, setLoadingServices] = useState(true);
  const [error, setError] = useState(null);
  const [selectedCapabilities, setSelectedCapabilities] = useState([]);
  const [selectedPhases, setSelectedPhases] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [healthStatus, setHealthStatus] = useState(null);

  const loadServices = useCallback(async () => {
    try {
      setLoadingServices(true);
      setError(null);

      const health = await phase4Client.healthCheck();
      setHealthStatus(health);

      const response = await phase4Client.serviceRegistryClient.listServices();

      const agentsList = response.agents || [];

      const transformedServices = agentsList.map((agent) => ({
        id: agent.name,
        name: agent.name,
        category: agent.category || 'general',
        description: agent.description || 'No description',
        phases: agent.phases || [],
        capabilities: agent.capabilities || [],
        version: agent.version || '1.0.0',
        actions: agent.actions || [],
      }));

      setServices(transformedServices);
    } catch (err) {
      const errorMessage = err.message || 'Failed to load services';
      const msg = `Error loading services: ${errorMessage}`;
      setError(msg);
      logger.error('[useServiceDiscovery] error:', err);
      if (onError) {
        onError(msg);
      }
    } finally {
      setLoadingServices(false);
    }
  }, [onError]);

  const allCapabilities = useMemo(
    () => Array.from(new Set(services.flatMap((s) => s.capabilities))).sort(),
    [services]
  );

  const allPhases = useMemo(
    () => Array.from(new Set(services.flatMap((s) => s.phases))).sort(),
    [services]
  );

  const filteredServices = useMemo(() => {
    return services.filter((service) => {
      const matchesSearch =
        searchQuery === '' ||
        service.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        service.description.toLowerCase().includes(searchQuery.toLowerCase());

      const matchesCapabilities =
        selectedCapabilities.length === 0 ||
        selectedCapabilities.some((cap) => service.capabilities.includes(cap));

      const matchesPhases =
        selectedPhases.length === 0 ||
        selectedPhases.some((phase) => service.phases.includes(phase));

      return matchesSearch && matchesCapabilities && matchesPhases;
    });
  }, [services, searchQuery, selectedCapabilities, selectedPhases]);

  const clearError = useCallback(() => setError(null), []);

  return {
    services,
    loadingServices,
    error,
    selectedCapabilities,
    setSelectedCapabilities,
    selectedPhases,
    setSelectedPhases,
    searchQuery,
    setSearchQuery,
    healthStatus,
    allCapabilities,
    allPhases,
    filteredServices,
    loadServices,
    clearError,
  };
};

export default useServiceDiscovery;
