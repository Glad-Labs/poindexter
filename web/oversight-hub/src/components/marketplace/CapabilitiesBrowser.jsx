import React, { useState, useEffect } from 'react';
import {
  Box,
  Card,
  CardContent,
  CardHeader,
  CircularProgress,
  Alert,
  Stack,
  TextField,
  Grid,
  Typography,
  Chip,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  InputAdornment,
} from '@mui/material';
import {
  Search as SearchIcon,
  ExpandMore as ExpandMoreIcon,
  Code as CodeIcon,
} from '@mui/icons-material';
import { getServiceRegistry } from '../../services/capabilityService';

/**
 * CapabilitiesBrowser Component (Phase 3.1)
 *
 * Browse and explore available agent capabilities:
 * - View all registered services
 * - Search capabilities by name or description
 * - View action details including parameters and response formats
 * - Filter by capability type
 */
export const CapabilitiesBrowser = () => {
  const [registry, setRegistry] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedService, setSelectedService] = useState(null);
  const [detailsOpen, setDetailsOpen] = useState(false);

  useEffect(() => {
    loadCapabilities();
  }, []);

  const loadCapabilities = async () => {
    setLoading(true);
    setError(null);

    try {
      const result = await getServiceRegistry();
      setRegistry(result);
    } catch (err) {
      setError(`Failed to load capabilities: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const getFilteredServices = () => {
    if (!registry?.services) return [];

    return Object.entries(registry.services).filter(([name, service]) => {
      const query = searchTerm.toLowerCase();
      return (
        name.toLowerCase().includes(query) ||
        service.description?.toLowerCase().includes(query) ||
        service.actions?.some(
          (a) =>
            a.name.toLowerCase().includes(query) ||
            a.description?.toLowerCase().includes(query)
        )
      );
    });
  };

  const handleViewDetails = (serviceName, service) => {
    setSelectedService({ name: serviceName, ...service });
    setDetailsOpen(true);
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  const filteredServices = getFilteredServices();

  return (
    <Box sx={{ width: '100%' }}>
      {/* Header */}
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" sx={{ fontWeight: 'bold', mb: 2 }}>
          Capabilities Browser
        </Typography>
        <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
          Explore all available agent capabilities and integrations
        </Typography>

        {/* Search Bar */}
        <TextField
          fullWidth
          placeholder="Search capabilities..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon />
              </InputAdornment>
            ),
          }}
        />
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {/* Stats Card */}
      <Card sx={{ mb: 3, backgroundColor: '#f5f5f5' }}>
        <CardContent>
          <Grid container spacing={2}>
            <Grid item xs={12} sm={6} md={4}>
              <Box>
                <Typography variant="subtitle2" color="textSecondary">
                  Total Services
                </Typography>
                <Typography variant="h4">
                  {Object.keys(registry?.services || {}).length}
                </Typography>
              </Box>
            </Grid>

            <Grid item xs={12} sm={6} md={4}>
              <Box>
                <Typography variant="subtitle2" color="textSecondary">
                  Total Capabilities
                </Typography>
                <Typography variant="h4">
                  {Object.values(registry?.services || {}).reduce(
                    (sum, s) => sum + (s.actions?.length || 0),
                    0
                  )}
                </Typography>
              </Box>
            </Grid>

            <Grid item xs={12} sm={6} md={4}>
              <Box>
                <Typography variant="subtitle2" color="textSecondary">
                  Matching Search
                </Typography>
                <Typography variant="h4">{filteredServices.length}</Typography>
              </Box>
            </Grid>
          </Grid>
        </CardContent>
      </Card>

      {/* Services List */}
      {filteredServices.length === 0 && (
        <Alert severity="info">
          {searchTerm
            ? 'No capabilities match your search'
            : 'No capabilities found'}
        </Alert>
      )}

      {filteredServices.length > 0 && (
        <Stack spacing={2}>
          {filteredServices.map(([serviceName, service]) => (
            <Card key={serviceName}>
              <CardHeader
                title={serviceName}
                subheader={service.description || 'No description'}
                action={
                  <Button
                    size="small"
                    endIcon={<ExpandMoreIcon />}
                    onClick={() => handleViewDetails(serviceName, service)}
                  >
                    View Actions
                  </Button>
                }
              />
              <CardContent>
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>
                    Capabilities ({service.actions?.length || 0})
                  </Typography>
                  <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
                    {service.actions?.map((action) => (
                      <Chip
                        key={action.name}
                        label={action.name}
                        size="small"
                        icon={<CodeIcon />}
                        variant="outlined"
                      />
                    )) || <Typography variant="caption">No actions</Typography>}
                  </Stack>
                </Box>
              </CardContent>
            </Card>
          ))}
        </Stack>
      )}

      {/* Service Details Dialog */}
      <Dialog
        open={detailsOpen}
        onClose={() => setDetailsOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>{selectedService?.name}</DialogTitle>
        <DialogContent sx={{ pt: 2 }}>
          <Stack spacing={3}>
            <Box>
              <Typography
                variant="subtitle1"
                sx={{ fontWeight: 'bold', mb: 1 }}
              >
                Description
              </Typography>
              <Typography variant="body2">
                {selectedService?.description || 'No description available'}
              </Typography>
            </Box>

            {selectedService?.actions && selectedService.actions.length > 0 && (
              <Box>
                <Typography
                  variant="subtitle1"
                  sx={{ fontWeight: 'bold', mb: 2 }}
                >
                  Available Actions
                </Typography>

                <TableContainer>
                  <Table size="small">
                    <TableHead>
                      <TableRow sx={{ backgroundColor: '#f5f5f5' }}>
                        <TableCell>Action Name</TableCell>
                        <TableCell>Description</TableCell>
                        <TableCell>Parameters</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {selectedService.actions.map((action) => (
                        <TableRow key={action.name}>
                          <TableCell>
                            <Chip label={action.name} size="small" />
                          </TableCell>
                          <TableCell>
                            <Typography variant="caption">
                              {action.description || 'No description'}
                            </Typography>
                          </TableCell>
                          <TableCell>
                            {action.parameters?.properties ? (
                              <Chip
                                label={`${Object.keys(action.parameters.properties).length} params`}
                                size="small"
                                variant="outlined"
                              />
                            ) : (
                              <Typography variant="caption">-</Typography>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              </Box>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDetailsOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default CapabilitiesBrowser;
