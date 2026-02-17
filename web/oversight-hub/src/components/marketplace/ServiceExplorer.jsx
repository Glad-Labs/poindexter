import React, { useState, useEffect } from 'react';
import {
  Box,
  Card,
  CardContent,
  CardHeader,
  CircularProgress,
  Alert,
  Stack,
  Button,
  Grid,
  Typography,
  Chip,
  Tab,
  Tabs,
  Paper,
  List,
  ListItem,
  ListItemText,
  Divider,
} from '@mui/material';
import {
  CheckCircle as CheckCircleIcon,
  Info as InfoIcon,
  Lightbulb as LightbulbIcon,
} from '@mui/icons-material';
import {
  listServices,
  getServiceMetadata,
} from '../../services/capabilityService';

/**
 * ServiceExplorer Component (Phase 3.2)
 *
 * Explore and understand available services:
 * - Browse services with detailed docs
 * - View capabilities and use cases
 * - Understand service relationships
 * - Access service-specific configurations
 */
export const ServiceExplorer = () => {
  const [services, setServices] = useState([]);
  const [selectedService, setSelectedService] = useState(null);
  const [serviceDetails, setServiceDetails] = useState(null);
  const [loading, setLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState(0);

  useEffect(() => {
    loadServices();
  }, []);

  useEffect(() => {
    if (selectedService) {
      loadServiceDetails(selectedService);
    }
  }, [selectedService]);

  const loadServices = async () => {
    setLoading(true);
    setError(null);

    try {
      const result = await listServices();
      setServices(Array.isArray(result) ? result : []);
      if (result.length > 0) {
        setSelectedService(result[0]);
      }
    } catch (err) {
      setError(`Failed to load services: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const loadServiceDetails = async (serviceName) => {
    setDetailsLoading(true);

    try {
      const details = await getServiceMetadata(serviceName);
      setServiceDetails(details);
    } catch (err) {
      console.error(`Failed to load service details: ${err.message}`);
      setServiceDetails(null);
    } finally {
      setDetailsLoading(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ width: '100%' }}>
      {/* Header */}
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" sx={{ fontWeight: 'bold', mb: 1 }}>
          Service Explorer
        </Typography>
        <Typography variant="body2" color="textSecondary">
          Discover and understand available AI services and integrations
        </Typography>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {/* Main Layout */}
      <Grid container spacing={3}>
        {/* Services List Sidebar */}
        <Grid item xs={12} md={3}>
          <Card>
            <CardHeader title={`Services (${services.length})`} />
            <List sx={{ maxHeight: 600, overflow: 'auto' }}>
              {services.map((service, idx) => (
                <React.Fragment key={service}>
                  <ListItem
                    button
                    selected={selectedService === service}
                    onClick={() => setSelectedService(service)}
                    sx={{
                      backgroundColor:
                        selectedService === service ? '#f5f5f5' : 'transparent',
                      '&:hover': { backgroundColor: '#f5f5f5' },
                    }}
                  >
                    <ListItemText primary={service} />
                  </ListItem>
                  {idx < services.length - 1 && <Divider />}
                </React.Fragment>
              ))}
            </List>
          </Card>
        </Grid>

        {/* Service Details Panel */}
        <Grid item xs={12} md={9}>
          {selectedService && detailsLoading && (
            <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
              <CircularProgress />
            </Box>
          )}

          {selectedService && !detailsLoading && serviceDetails && (
            <Stack spacing={3}>
              {/* Service Header Card */}
              <Card>
                <CardHeader
                  title={selectedService}
                  subheader={serviceDetails.description || 'No description'}
                  avatar={<CheckCircleIcon sx={{ color: '#4caf50' }} />}
                />
                <CardContent>
                  <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
                    <Chip label="Active" color="success" size="small" />
                    <Chip
                      label={`${serviceDetails.actions?.length || 0} Actions`}
                      variant="outlined"
                      size="small"
                    />
                    <Chip label="REST API" variant="outlined" size="small" />
                  </Stack>
                </CardContent>
              </Card>

              {/* Tabs */}
              <Paper>
                <Tabs
                  value={activeTab}
                  onChange={(e, newTab) => setActiveTab(newTab)}
                >
                  <Tab label="Overview" />
                  <Tab label="Actions" />
                  <Tab label="Use Cases" />
                </Tabs>

                <Box sx={{ p: 3 }}>
                  {/* Tab 0: Overview */}
                  {activeTab === 0 && (
                    <Stack spacing={2}>
                      <Box>
                        <Typography
                          variant="subtitle1"
                          sx={{ fontWeight: 'bold', mb: 1 }}
                        >
                          About This Service
                        </Typography>
                        <Typography variant="body2">
                          {serviceDetails.description ||
                            'This service provides integrated functionality within the AI orchestration system.'}
                        </Typography>
                      </Box>

                      <Divider />

                      <Box>
                        <Typography
                          variant="subtitle1"
                          sx={{ fontWeight: 'bold', mb: 1 }}
                        >
                          Service Information
                        </Typography>
                        <Stack spacing={1}>
                          <Box
                            sx={{
                              display: 'flex',
                              justifyContent: 'space-between',
                            }}
                          >
                            <Typography variant="body2">
                              Service Name:
                            </Typography>
                            <Typography
                              variant="body2"
                              sx={{ fontWeight: 'bold' }}
                            >
                              {selectedService}
                            </Typography>
                          </Box>
                          <Box
                            sx={{
                              display: 'flex',
                              justifyContent: 'space-between',
                            }}
                          >
                            <Typography variant="body2">
                              Available Actions:
                            </Typography>
                            <Typography
                              variant="body2"
                              sx={{ fontWeight: 'bold' }}
                            >
                              {serviceDetails.actions?.length || 0}
                            </Typography>
                          </Box>
                          <Box
                            sx={{
                              display: 'flex',
                              justifyContent: 'space-between',
                            }}
                          >
                            <Typography variant="body2">Status:</Typography>
                            <Chip
                              label="Operational"
                              size="small"
                              color="success"
                            />
                          </Box>
                        </Stack>
                      </Box>
                    </Stack>
                  )}

                  {/* Tab 1: Actions */}
                  {activeTab === 1 && (
                    <Stack spacing={2}>
                      {serviceDetails.actions?.map((action) => (
                        <Card key={action.name} variant="outlined">
                          <CardHeader
                            title={action.name}
                            subheader={action.description || 'No description'}
                          />
                          <CardContent>
                            {action.parameters && (
                              <Box sx={{ mb: 2 }}>
                                <Typography
                                  variant="subtitle2"
                                  sx={{ fontWeight: 'bold' }}
                                >
                                  Parameters
                                </Typography>
                                {action.parameters.properties ? (
                                  <List dense>
                                    {Object.entries(
                                      action.parameters.properties
                                    ).map(([paramName, paramDef]) => (
                                      <ListItem key={paramName}>
                                        <ListItemText
                                          primary={paramName}
                                          secondary={
                                            paramDef.type ||
                                            paramDef.description ||
                                            'Unknown'
                                          }
                                        />
                                      </ListItem>
                                    ))}
                                  </List>
                                ) : (
                                  <Typography variant="caption">
                                    No parameters
                                  </Typography>
                                )}
                              </Box>
                            )}

                            {action.response && (
                              <Box>
                                <Typography
                                  variant="subtitle2"
                                  sx={{ fontWeight: 'bold' }}
                                >
                                  Response Format
                                </Typography>
                                <Typography variant="caption">
                                  {typeof action.response === 'string'
                                    ? action.response
                                    : JSON.stringify(action.response, null, 2)}
                                </Typography>
                              </Box>
                            )}
                          </CardContent>
                        </Card>
                      )) || (
                        <Alert severity="info">
                          No actions available for this service
                        </Alert>
                      )}
                    </Stack>
                  )}

                  {/* Tab 2: Use Cases */}
                  {activeTab === 2 && (
                    <Stack spacing={2}>
                      <Box
                        sx={{
                          display: 'flex',
                          gap: 1,
                          alignItems: 'flex-start',
                        }}
                      >
                        <LightbulbIcon sx={{ color: '#ff9800', mt: 1 }} />
                        <Box>
                          <Typography
                            variant="subtitle2"
                            sx={{ fontWeight: 'bold' }}
                          >
                            Common Use Cases
                          </Typography>
                          <Typography variant="body2">
                            This service is commonly used for:
                          </Typography>
                          <List dense>
                            <ListItem>
                              <ListItemText
                                primary="Task Automation"
                                secondary="Automate repetitive workflows and processes"
                              />
                            </ListItem>
                            <ListItem>
                              <ListItemText
                                primary="Integration"
                                secondary="Connect with other systems and services"
                              />
                            </ListItem>
                            <ListItem>
                              <ListItemText
                                primary="Data Processing"
                                secondary="Transform and manage data efficiently"
                              />
                            </ListItem>
                          </List>
                        </Box>
                      </Box>

                      <Box
                        sx={{
                          display: 'flex',
                          gap: 1,
                          alignItems: 'flex-start',
                        }}
                      >
                        <InfoIcon sx={{ color: '#2196f3', mt: 1 }} />
                        <Box>
                          <Typography
                            variant="subtitle2"
                            sx={{ fontWeight: 'bold' }}
                          >
                            Pro Tips
                          </Typography>
                          <List dense>
                            <ListItem>
                              <ListItemText secondary="Combine multiple actions for complex workflows" />
                            </ListItem>
                            <ListItem>
                              <ListItemText secondary="Check action parameters for customization options" />
                            </ListItem>
                          </List>
                        </Box>
                      </Box>
                    </Stack>
                  )}
                </Box>
              </Paper>
            </Stack>
          )}

          {selectedService && !detailsLoading && !serviceDetails && (
            <Alert severity="warning">
              Failed to load service details. Please try again.
            </Alert>
          )}
        </Grid>
      </Grid>

      {/* Refresh Button */}
      <Box sx={{ mt: 3, display: 'flex', justifyContent: 'center' }}>
        <Button variant="outlined" onClick={loadServices}>
          Refresh Services
        </Button>
      </Box>
    </Box>
  );
};

export default ServiceExplorer;
